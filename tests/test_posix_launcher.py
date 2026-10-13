"""Linux/POSIX launcher, portable server flags, and the real downloader.

These run on any POSIX shell (Ubuntu's /bin/sh is dash) and on macOS; the Windows
equivalents live in tests/test_windows_install.py.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_installer
import download_game
import serve_local

HERE = Path(__file__).resolve().parents[1]
POSIX = os.name != 'nt'


def shell_scripts():
    """Every shell script in the repository, including the double-click wrappers."""
    found = {name for name in os.listdir(HERE) if name.endswith(('.sh', '.command'))}
    return sorted(found)


class LauncherScriptTests(unittest.TestCase):
    @unittest.skipUnless(POSIX, 'POSIX shell checks need a Unix shell')
    def test_shell_scripts_are_posix_valid(self):
        """A bash-only or CRLF script dies on Ubuntu with 'bad interpreter' or a syntax error."""
        for name in shell_scripts():
            path = HERE / name
            with self.subTest(script=name):
                text = path.read_bytes()
                self.assertNotIn(b'\r\n', text, '%s must use LF line endings' % name)
                self.assertEqual(text[:2], b'#!', '%s needs a shebang' % name)
                subprocess.run(['/bin/sh', '-n', str(path)], check=True, capture_output=True)

    @unittest.skipUnless(POSIX, 'POSIX shell checks need a Unix shell')
    def test_linux_launcher_help_and_unknown_command(self):
        help_result = subprocess.run(['sh', str(HERE / 'linux-launcher.sh'), 'help'],
                                     capture_output=True, text=True, check=True)
        for word in ('download', 'launch', 'lan', 'background', 'service', 'deps'):
            self.assertIn(word, help_result.stdout)
        bad = subprocess.run(['sh', str(HERE / 'linux-launcher.sh'), 'definitely-not-a-command'],
                             capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('tidak dikenal', bad.stderr)

    @unittest.skipUnless(POSIX, 'POSIX shell checks need a Unix shell')
    def test_menu_offers_the_numbered_setup_flow(self):
        """The whole point: a fresh Linux box gets 1/2/3 instead of reading a manual."""
        result = subprocess.run(['sh', str(HERE / 'linux-launcher.sh'), 'menu'], input='',
                                capture_output=True, text=True, timeout=60, cwd=str(HERE))
        self.assertEqual(result.returncode, 0, result.stderr[-400:])
        for line in ('1) Cek sistem', '2) Download game', '3) Verifikasi', '4) Main sekarang',
                     '5) Main dari HP', '10) Pasang yang kurang'):
            self.assertIn(line, result.stdout)
        # a non-tty run must fall back to the plain report instead of waiting for a keyboard
        plain = subprocess.run(['sh', str(HERE / 'linux-launcher.sh')], input='', capture_output=True,
                               text=True, timeout=60, cwd=str(HERE))
        self.assertEqual(plain.returncode, 0, plain.stderr[-400:])
        self.assertIn('python', plain.stdout)

    @unittest.skipUnless(POSIX, 'POSIX shell checks need a Unix shell')
    def test_bootstrap_installer_is_safe_to_pipe_and_posix_valid(self):
        install = HERE / 'linux-install.sh'
        self.assertTrue(install.is_file())
        subprocess.run(['/bin/sh', '-n', str(install)], check=True, capture_output=True)
        text = install.read_text(encoding='utf-8')
        self.assertNotIn('\r\n', text)
        for word in ('TARGET', 'linux-launcher.sh', 'denllowss/playgta5'):
            self.assertIn(word, text)

    def test_installer_bundle_carries_the_linux_entry_point(self):
        """Without these in the small installer ZIP a Linux user has no launcher at all."""
        for name in ('linux-launcher.sh', 'linux-install.sh', 'README-LINUX.md'):
            self.assertIn(name, build_installer.FILES)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'installer.zip'
            build_installer.build(output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('PlayGTA5/linux-launcher.sh'), (HERE / 'linux-launcher.sh').read_bytes())
                self.assertTrue(archive.getinfo('PlayGTA5/linux-launcher.sh').external_attr >> 16 & 0o111,
                                'the launcher must keep its executable bit inside the ZIP')


class PortableServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous_root = serve_local.ROOT
        cls.previous_case = serve_local.CASE_INSENSITIVE
        cls.previous_quiet = serve_local.QUIET
        serve_local.ROOT = Path(cls.temp.name)
        (serve_local.ROOT / 'data').mkdir()
        (serve_local.ROOT / 'data' / 'sample').write_bytes(b'0123456789')
        # Linux keeps file-name case; a mirror copied from NTFS may not match the URLs.
        (serve_local.ROOT / 'GAME.HTML').write_bytes(b'<!doctype html>game')
        serve_local.CASE_INSENSITIVE = True
        serve_local.QUIET = True
        cls.server = serve_local.make_server('127.0.0.1', 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        serve_local.ROOT = cls.previous_root
        serve_local.CASE_INSENSITIVE = cls.previous_case
        serve_local.QUIET = cls.previous_quiet

    def request(self, method, path, body=None, headers=None, connection=None):
        own = connection is None
        connection = connection or http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read(), connection
        finally:
            if own:
                connection.close()

    def test_case_insensitive_fallback_for_get(self):
        status, _, body, _ = self.request('GET', '/game.html')
        self.assertEqual((status, body), (200, b'<!doctype html>game'))
        serve_local.CASE_INSENSITIVE = False
        try:
            self.assertEqual(self.request('GET', '/game.html')[0], 404)
        finally:
            serve_local.CASE_INSENSITIVE = True

    def test_batch_read_survives_the_wrong_case(self):
        status, headers, body, _ = self.request('POST', '/data/batch', json.dumps([['SAMPLE', 2, 4]]))
        self.assertEqual((status, body, headers['X-Run-Lengths']), (200, b'234', '3'))
        self.assertIsNone(serve_local.Handler.data_file(self, serve_local.ROOT / 'data', '../index.html'))
        self.assertIsNone(serve_local.Handler.data_file(self, serve_local.ROOT / 'data', '/etc/passwd'))
        self.assertIsNone(serve_local.Handler.data_file(self, serve_local.ROOT / 'data', 'sample/../index.html'))

    def test_keep_alive_stays_in_sync_after_refused_posts(self):
        """HTTP/1.1 means a refused body must still be read, or the next response is garbage."""
        serve_local.QUIET = True
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        cases = [('/data/batch', json.dumps([['sample', 'x', 1]])),        # 400, bad types
                 ('/nope', json.dumps([['sample', 0, 1]])),                 # 404, unknown path
                 ('/data/batch', 'x' * (2 * 1024 * 1024))]                  # 413, oversized
        for path, payload in cases:
            with self.subTest(path=path):
                data = payload.encode()
                connection.request('POST', path, data, {'Content-Length': str(len(data))})
                response = connection.getresponse()
                self.assertIn(response.status, (400, 404, 413))
                response.read()
        status, _, body, _ = self.request('GET', '/game.html', connection=connection)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'<!doctype html>game')

    def test_http_one_one_is_used(self):
        status, headers, _, _ = self.request('GET', '/data/sample', headers={'Range': 'bytes=0-1'})
        self.assertEqual(status, 206)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('HEAD', '/data/sample')
            response = connection.getresponse()
            self.assertEqual(response.version, 11)
            self.assertEqual(response.status, 200)
        finally:
            connection.close()

    def test_root_resolution_prefers_the_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory) / 'mirror' / 'playgta5.com'
            other.mkdir(parents=True)
            (other / 'index.html').write_text('game')
            os.environ['PLAYGTA5_MIRROR'] = str(other)
            self.addCleanup(os.environ.pop, 'PLAYGTA5_MIRROR', None)
            self.assertEqual(serve_local.resolve_root(), other.resolve())
            # a folder that merely contains mirror/playgta5.com is enough
            self.assertEqual(serve_local.resolve_root(str(Path(directory))), other.resolve())
            nested_target = Path(directory) / 'elsewhere'
            nested_target.mkdir()
            self.assertEqual(serve_local.resolve_root(str(nested_target)), nested_target.resolve())

    def test_check_reports_a_missing_installation(self):
        result = subprocess.run([sys.executable, str(HERE / 'serve_local.py'), '--check',
                                '--root', str(HERE / 'no-such-mirror-here')],
                               capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('download', result.stdout + result.stderr)

    @unittest.skipUnless(POSIX, 'headless behaviour is a Linux/macOS concern')
    def test_open_needs_no_display(self):
        """A headless server must print the address instead of raising from xdg-open."""
        saved = {key: os.environ.get(key) for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'BROWSER')}
        for key in saved:
            os.environ.pop(key, None)
        try:
            serve_local.open_browser_later('http://localhost:8000/__support/')
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value


@unittest.skipUnless(POSIX and shutil.which('curl'), 'the downloader is exercised through curl')
class DownloaderTests(unittest.TestCase):
    """Install from a local HTTP server, including a half-finished file already present."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.release = base / 'release'
        self.release.mkdir()
        self.root = base / 'install-here'
        self.root.mkdir()
        index = b'<!doctype html>game'
        archive = self.release / 'part-01.zip'
        records = []
        with zipfile.ZipFile(archive, 'w') as bundle:
            payload = {'mirror/playgta5.com/index.html': index,
                       'runtime/python.exe': b'windows-only',
                       'README.md': b'release helper'}
            (self.root / 'README.md').write_text('current helper')
            for name, data in payload.items():
                bundle.writestr(name, data)
                records.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                                'part': archive.name})
        self.manifest = {'version': 1, 'parts': [{'name': archive.name, 'bytes': archive.stat().st_size,
                                                  'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                                                  'url': None}]}
        self.inventory = {'version': 1, 'files': records}
        (base / 'manifest.json').write_text(json.dumps(self.manifest))
        (base / 'inventory.json').write_text(json.dumps(self.inventory))
        self.manifest_path = base / 'manifest.json'
        self.inventory_path = base / 'inventory.json'
        # serve the archive with the project's own handler: it answers byte ranges,
        # which is what makes curl's resume work
        self.previous_quiet = serve_local.QUIET
        serve_local.QUIET = True
        self.addCleanup(setattr, serve_local, 'QUIET', self.previous_quiet)
        release = self.release

        self.server = ThreadingHTTPServer(('127.0.0.1', 0),
                                          lambda *a, **k: serve_local.Handler(*a, directory=str(release), **k))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        url = 'http://127.0.0.1:%d/%s' % (self.server.server_port, archive.name)
        manifest = json.loads(self.manifest_path.read_text())
        manifest['parts'][0]['url'] = url
        self.manifest_path.write_text(json.dumps(manifest))

    def install(self, **kwargs):
        download_game.install(self.root, self.manifest_path, self.inventory_path, **kwargs)

    def test_install_skips_helpers_and_foreign_runtime(self):
        self.install()
        self.assertEqual((self.root / 'mirror/playgta5.com/index.html').read_bytes(), b'<!doctype html>game')
        self.assertFalse((self.root / 'runtime').exists(), 'the Windows runtime must not be installed')
        self.assertEqual((self.root / 'README.md').read_text(), 'current helper', 'updated helpers are preserved')
        self.assertFalse((self.root / '.downloads' / 'part-01.zip').exists(), 'verified archives are removed')
        self.assertTrue((self.root / '.installed-parts' / 'part-01.zip.sha256').is_file())

    def test_completed_parts_are_not_downloaded_again(self):
        self.install()
        before = (self.root / 'mirror/playgta5.com/index.html').stat().st_mtime_ns
        self.install()
        self.assertEqual((self.root / 'mirror/playgta5.com/index.html').stat().st_mtime_ns, before)

    def test_resumes_an_interrupted_download(self):
        archive = self.release / 'part-01.zip'
        partial = self.root / '.downloads' / 'part-01.zip.partial'
        partial.parent.mkdir(parents=True, exist_ok=True)
        cut = archive.stat().st_size // 2
        partial.write_bytes(archive.read_bytes()[:cut])
        self.install()
        self.assertEqual((self.root / 'mirror/playgta5.com/index.html').read_bytes(), b'<!doctype html>game')
        self.assertFalse(partial.exists(), 'the partial file is consumed when the part finishes')

    def test_keep_archives_leaves_the_verified_zip(self):
        self.install(keep_archives=True)
        self.assertTrue((self.root / '.downloads' / 'part-01.zip').is_file())

    def test_corrupt_archive_is_refused(self):
        broken = self.release / 'part-01.zip'
        broken.write_bytes(broken.read_bytes()[:100] + b'garbage' + broken.read_bytes()[200:])
        with self.assertRaises(Exception):
            self.install()
        self.assertFalse((self.root / 'mirror/playgta5.com/index.html').exists())


@unittest.skipUnless(POSIX and shutil.which('openssl'), 'the LAN certificate needs openssl')
class TlsCertificateTests(unittest.TestCase):
    def test_self_signed_certificate_is_created_once_per_host_list(self):
        with tempfile.TemporaryDirectory() as directory:
            certs = Path(directory)
            cert, key = serve_local.ensure_self_signed(certs, extra_hosts=('10.0.0.5',))
            self.assertTrue(cert.is_file() and key.is_file())
            self.assertIn('10.0.0.5', (certs / 'hosts.txt').read_text())
            first = cert.read_bytes()
            serve_local.ensure_self_signed(certs, extra_hosts=('10.0.0.5',))
            self.assertEqual(cert.read_bytes(), first, 'an unchanged host list must not reissue the certificate')
            serve_local.ensure_self_signed(certs, extra_hosts=('10.0.0.6',))
            self.assertNotIn('10.0.0.5', (certs / 'hosts.txt').read_text())
            result = subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout', '-ext', 'subjectAltName'],
                                    capture_output=True, text=True)
            self.assertIn('10.0.0.6', result.stdout)


class DownloadCliTests(unittest.TestCase):
    @unittest.skipUnless(POSIX, 'the CLI is checked as a shell command')
    def test_root_flag_is_accepted_by_both_entry_points(self):
        for script, flag in (('download_game.py', '--root'), ('serve_local.py', '--host'),
                             ('serve_local.py', '--tls'), ('serve_local.py', '--check')):
            with self.subTest(script=script, flag=flag):
                usage = subprocess.run([sys.executable, str(HERE / script), '--help'],
                                       capture_output=True, text=True, check=True)
                self.assertIn(flag, usage.stdout)


if __name__ == '__main__':
    unittest.main()
