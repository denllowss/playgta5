"""Local mirror server: isolation headers, byte ranges, and engine batch I/O.

Runs on Windows, macOS and Linux (Ubuntu/Debian, Fedora, Arch, openSUSE) with
standard-library Python 3.11 or newer.

    Windows      double-click Launch-Local.cmd
    macOS        bash Launch-Local.command
    Linux        ./linux-launcher.sh launch        (see README-LINUX.md)
    any system   python3 serve_local.py [--root DIR] [--port 8000] [--open]

Portable extras on this server: --host/--tls for another device on the LAN,
--check to report which mirror folder was found, a case-insensitive filename
fallback (Linux is case sensitive, a copied mirror often is not), HTTP/1.1
keep-alive for the engine's thousands of small reads, and no crash when opening
a browser is impossible on a headless machine.
"""
import argparse, gzip, io, json, mimetypes, os, re, shutil, socket, ssl, subprocess, sys, threading, time, webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs

HERE = Path(__file__).resolve().parent
SUPPORT = HERE / 'support'
MIRROR_ENV = 'PLAYGTA5_MIRROR'
ROOT = HERE / 'mirror' / 'playgta5.com'
if not (ROOT / 'index.html').is_file():
    nested_root = ROOT.parent / 'mirror' / 'playgta5.com'
    if (nested_root / 'index.html').is_file():
        ROOT = nested_root
CASE_INSENSITIVE = True
QUIET = False
_tls_context = None

mimetypes.add_type('application/wasm', '.wasm')
mimetypes.add_type('text/javascript', '.js')


def resolve_root(override=None):
    """Find the game folder: --root, $PLAYGTA5_MIRROR, ./mirror/playgta5.com, ./mirror."""
    explicit = [raw for raw in (override, os.environ.get(MIRROR_ENV)) if raw]
    for raw, given in [(r, True) for r in explicit] + [(HERE / 'mirror' / 'playgta5.com', False), (HERE / 'mirror', False)]:
        path = Path(os.path.expandvars(str(raw))).expanduser()
        if not path.is_absolute():
            path = HERE / path
        if not path.is_dir():
            continue
        if (path / 'index.html').is_file():
            return path.resolve()
        nested = path / 'mirror' / 'playgta5.com'
        if (nested / 'index.html').is_file():
            return nested.resolve()
        if given:                       # an explicit --root is trusted (it may be a custom layout)
            return path.resolve()
    return None


def describe_root(root=None):
    """Mirror summary for --check: file count and bytes, sampled to stay fast."""
    root = Path(root or ROOT)
    files = 0
    total = 0
    for path in root.rglob('*'):
        if path.is_file():
            files += 1
            total += path.stat().st_size
            if files >= 5000:
                return '%d+ files' % files, '%d+ MiB' % (total // 2 ** 20)
    return '%d files' % files, '%d MiB' % (total // 2 ** 20)


def improve_game_page(code):
    """Improve support text at serving/build time, preserving verified release files."""
    if 'game-progress' not in code:
        return code
    code = code.replace(': the device may be out of memory. Close other tabs, or reload.',
                        ': loading has paused. Use Help / browser checks and collect the first console error.')
    code = code.replace('If the tab shows "Aw, Snap!" or SIGILL it ran out of memory.',
                        'A tab crash needs its error details; it does not by itself prove insufficient RAM.')
    link = '<a href="/__support/" target="_blank" rel="noopener" style="position:fixed;top:12px;right:12px;z-index:10000;background:#182621;color:#fff;padding:10px 14px;border-radius:5px;font:14px system-ui;text-decoration:none">Help / browser checks</a>'
    return code.replace('<body>', '<body>\n' + link, 1)


_fold_cache = {}


def folded_names(directory):
    """{casefolded name without trailing dots/spaces: real name} for one directory."""
    names = _fold_cache.get(directory)
    if names is None:
        try:
            listing = os.listdir(directory)
        except OSError:
            listing = []
        names = {}
        for name in listing:
            names.setdefault(name.casefold().rstrip('. '), name)
        if len(_fold_cache) < 4096:
            _fold_cache[directory] = names
    return names


class Handler(SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'          # keep-alive: the engine opens thousands of small reads
    server_version = 'PlayGTA5-Local/1.0 (Python %s)' % '.'.join(map(str, sys.version_info[:3]))

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def __init__(self, *args, directory=None, **kwargs):
        self.byte_range = None
        # the module ROOT is the default so tests and tools can point one Handler
        # somewhere else without touching the global
        super().__init__(*args, directory=str(Path(directory) if directory else ROOT), **kwargs)

    def log_message(self, fmt, *args):
        if not QUIET:
            sys.stderr.write('%s %s\n' % (time.strftime('%H:%M:%S'), fmt % args))

    def end_headers(self):
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()

    # ---- paths ------------------------------------------------------------ #
    def translate_path(self, path):
        resolved = super().translate_path(path)
        if not CASE_INSENSITIVE or os.path.lexists(resolved):
            return resolved
        fallback = self.with_different_case(resolved)
        return fallback if fallback is not None else resolved

    def with_different_case(self, resolved):
        """Retry a path one component at a time, ignoring letter case and the trailing
        dots/spaces a Windows copy drops. ext4/xfs keep both, so this rescues a mirror
        that was moved between filesystems."""
        base = Path(self.directory).resolve()
        try:
            relative = Path(resolved).relative_to(base)
        except ValueError:
            return None
        current = base
        for part in relative.parts:
            if part in ('', '.', '..'):
                continue
            direct = current / part
            if direct.exists() or direct.is_symlink():
                current = direct
                continue
            match = folded_names(str(current)).get(part.casefold().rstrip('. '))
            if match is None:
                return None
            current = current / match
        return str(current) if current != base else None

    def data_file(self, data_root, name):
        """Map an engine data path onto the mirror, refusing to leave the data folder."""
        parts = []
        for part in unquote(str(name)).replace('\\', '/').split('/'):
            if part in ('', '.'):
                continue
            if part == '..':
                if parts:
                    parts.pop()
                continue
            parts.append(part)
        if not parts:
            return None
        file = data_root.joinpath(*parts)
        if not file.is_relative_to(data_root):
            return None
        if file.is_file():
            return file if file.resolve().is_relative_to(data_root) else None
        if not CASE_INSENSITIVE:
            return None
        current = data_root
        for part in parts:
            direct = current / part
            if direct.exists() or direct.is_symlink():
                current = direct
                continue
            match = folded_names(str(current)).get(part.casefold().rstrip('. '))
            if match is None:
                return None
            current = current / match
        if not current.is_file() or not current.is_relative_to(data_root):
            return None
        return current if current.resolve().is_relative_to(data_root) else None

    # ---- GET / HEAD ------------------------------------------------------- #
    def send_head(self):
        self.byte_range = None
        if urlsplit(self.path).path in ('/', '/index.html') and (ROOT / 'index.html').is_file():
            body = improve_game_page((ROOT / 'index.html').read_text(encoding='utf-8')).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return io.BytesIO(body)
        support_files = {'/__support/': 'index.html', '/__support/check.js': 'check.js'}
        support_name = support_files.get(urlsplit(self.path).path)
        if support_name:
            path = SUPPORT / support_name
            if not path.is_file():
                self.send_error(404)
                return None
            self.send_response(200)
            self.send_header('Content-Type', self.guess_type(str(path)))
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return path.open('rb')
        if not self.headers.get('Range'):
            return super().send_head()
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return None
        size = path.stat().st_size
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers['Range'])
        if not match or not any(match.groups()):
            self.send_error(416)
            return None
        start = int(match[1]) if match[1] else max(0, size - int(match[2]))
        end = min(size - 1, int(match[2])) if match[1] and match[2] else size - 1
        if start >= size or end < start:
            self.send_response(416)
            self.send_header('Content-Range', 'bytes */%d' % size)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        self.byte_range = (start, end)
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        source = path.open('rb')
        source.seek(start)
        return source

    def copyfile(self, source, outputfile):
        if self.byte_range is None:
            return super().copyfile(source, outputfile)
        remaining = self.byte_range[1] - self.byte_range[0] + 1
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    # ---- engine batch reads ----------------------------------------------- #
    def do_POST(self):
        url = urlsplit(self.path)
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.drain(0)
            self.send_error(400, 'Invalid Content-Length')
            return
        # A kept-alive connection must not be left with an unread body.
        body = self.drain(length)
        if url.path != '/data/batch':
            self.send_error(404)
            return
        if not 0 <= length <= 1024 * 1024:
            self.send_error(413, 'Invalid batch size')
            return
        try:
            runs = json.loads(body)
            if not isinstance(runs, list) or len(runs) > 1000:
                raise ValueError('invalid batch')
            selected = []
            data_root = (ROOT / 'data').resolve()
            for name, start, end in runs:
                if not isinstance(name, str) or type(start) is not int or type(end) is not int:
                    raise ValueError('invalid file/range types')
                file = self.data_file(data_root, name)
                if file is None or start < 0 or end < start:
                    raise ValueError('invalid file/range')
                selected.append((file, start, max(0, min(end + 1, file.stat().st_size) - start)))
            total = sum(n for _, _, n in selected)
            if total > 64 * 1024 * 1024:
                raise ValueError('batch exceeds 64 MiB')
            output = io.BytesIO()
            for file, start, n in selected:
                with file.open('rb') as source:
                    source.seek(start)
                    output.write(source.read(n))
            body = output.getvalue()
            compressed = parse_qs(url.query).get('gz') == ['1']
            if compressed:
                body = gzip.compress(body, compresslevel=1)
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Run-Lengths', ','.join(str(n) for _, _, n in selected))
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, TypeError, OSError) as exc:
            self.send_error(400, str(exc))

    def drain(self, declared):
        """Consume (at most 4 MiB of) a request body so the next request on this
        connection stays in sync; more than that closes the connection instead."""
        if declared <= 0:
            return b''
        if declared > 4 * 1024 * 1024:
            self.close_connection = True
            return b''
        return self.rfile.read(declared)

    def guess_type(self, path):
        if str(path).endswith('.wasm'):
            return 'application/wasm'      # /etc/mime.types on Linux can disagree
        return super().guess_type(path)


# --------------------------------------------------------------------------- #
# serving on a LAN, and starting up
# --------------------------------------------------------------------------- #

def local_addresses():
    """Names a LAN client could actually type: hostname, primary IPv4, loopback."""
    found = {'localhost', '127.0.0.1', '::1', socket.gethostname()}
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('10.255.255.255', 1))       # nothing is sent; it only selects a route
        found.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(found)


def ensure_self_signed(certs_dir, extra_hosts=()):
    """Throwaway certificate: browsers only allow SharedArrayBuffer/WebGPU on a secure
    context, so serving a LAN address needs HTTPS. Regenerated when the host list changes."""
    certs_dir = Path(certs_dir)
    cert, key, marker = certs_dir / 'server.crt', certs_dir / 'server.key', certs_dir / 'hosts.txt'
    hosts = sorted(set(extra_hosts) | set(local_addresses()))
    wanted = '\n'.join(hosts) + '\n'
    if cert.is_file() and key.is_file() and marker.is_file() and marker.read_text() == wanted:
        return cert, key
    if shutil.which('openssl') is None:
        raise SystemExit('openssl is needed to create the self-signed certificate:\n'
                         '    sudo apt install openssl        (Debian/Ubuntu)\n'
                         '    sudo dnf install openssl        (Fedora)\n'
                         '    sudo pacman -S openssl          (Arch)\n'
                         'Or stay on http://localhost, which browsers already treat as secure.')
    certs_dir.mkdir(parents=True, exist_ok=True)
    alt = ','.join('IP:%s' % h if re.fullmatch(r'[0-9a-fA-F:.]+', h) else 'DNS:%s' % h for h in hosts)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
                    '-keyout', str(key), '-out', str(cert), '-subj', '/CN=playgta5.local',
                    '-addext', 'subjectAltName=' + alt], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    marker.write_text(wanted, encoding='ascii')
    os.chmod(key, 0o600)
    print('Self-signed certificate for: %s' % ', '.join(hosts), flush=True)
    return cert, key


def make_server(host, port, handler=Handler, tls_context=None):
    family = socket.AF_INET6 if ':' in host else socket.AF_INET

    class Server(ThreadingHTTPServer):
        address_family = family
        daemon_threads = True
        tls = tls_context                 # a class body may read, but not self-assign, the local

        def get_request(self, sock=None):
            # Python 3.14 hands the listening socket in; older versions use self.socket
            connection, address = (sock if sock is not None else self.socket).accept()
            if self.tls is None:
                return connection, address
            try:
                return self.tls.wrap_socket(connection, server_side=True), address
            except (ssl.SSLError, OSError, ValueError):
                connection.close()        # a probe, or a browser that dropped the handshake
                raise

    try:
        return Server((host, port), handler)
    except OSError as error:
        raise SystemExit('Cannot start on %s:%d: %s. Stop the previous game server (Ctrl+C or '
                         './linux-launcher.sh stop), or use --port 8001.' % (host, port, error))


def open_browser_later(url):
    """Open a browser when there is one. A headless Linux box (no DISPLAY, no xdg-open)
    gets the address printed instead of a crash or a silent failure."""
    graphical = sys.platform.startswith('win') or sys.platform == 'darwin' or bool(
        os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY') or os.environ.get('BROWSER'))
    if not graphical:
        print('No display found (headless session): open %s in a Chrome/Edge window yourself.' % url, flush=True)
        return

    def worker():
        time.sleep(0.4)
        try:
            if not webbrowser.open(url):
                raise OSError('no browser is registered for this desktop')
        except Exception as error:
            print('A browser could not be opened (%s). Open %s yourself in Chrome or Edge.' % (error, url), flush=True)

    threading.Thread(target=worker, daemon=True).start()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--root', type=Path, help='existing game directory containing index.html, b/, and data/'
                        ' (default: $%s, then ./mirror/playgta5.com)' % MIRROR_ENV)
    parser.add_argument('--host', default='127.0.0.1', help='address to listen on; 0.0.0.0 for other devices')
    parser.add_argument('--open', action='store_true', help='open browser compatibility and launch page')
    parser.add_argument('--tls', action='store_true', help='serve HTTPS with a self-signed certificate (needed off localhost)')
    parser.add_argument('--certs', type=Path, default=HERE / '.local-tls', help='folder for the self-signed certificate')
    parser.add_argument('--no-case-insensitive', action='store_true', help='disable the filename case fallback')
    parser.add_argument('--quiet', action='store_true', help='do not log each request')
    parser.add_argument('--check', action='store_true', help='report the game folder that was found, then exit')
    return parser.parse_args(argv)


def main(argv=None):
    global CASE_INSENSITIVE, QUIET, ROOT, _tls_context
    options = parse_args(argv)
    CASE_INSENSITIVE = not options.no_case_insensitive
    QUIET = options.quiet
    found = resolve_root(options.root or None)
    if found:
        ROOT = found
    elif options.root:
        ROOT = Path(options.root).resolve()      # keep the path in the error message
    if options.check:
        if not (ROOT / 'index.html').is_file():
            print('No game folder found (looked in $%s, %s).' % (MIRROR_ENV, HERE / 'mirror' / 'playgta5.com'))
            print('Run ./linux-launcher.sh download (or python3 download_game.py) first.')
            return 2
        count, size = describe_root()
        print('game folder : %s\nfiles       : %s (%s)\npython      : %s on %s'
              % (ROOT, count, size, sys.version.split()[0], sys.platform))
        return 0
    if not (ROOT / 'index.html').is_file():
        raise SystemExit('Game files are missing in %s.\n'
                         'Windows: Download-Game.cmd   macOS: bash Download-Game.command   '
                         'Linux: ./linux-launcher.sh download\n'
                         '(or python3 download_game.py --root /where/you/want/it)' % ROOT)
    if options.tls:
        cert, key = ensure_self_signed(options.certs, extra_hosts=()
                                       if options.host in ('0.0.0.0', '', '::') else (options.host,))
        _tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        _tls_context.load_cert_chain(str(cert), keyfile=str(key))
    server = make_server(options.host, options.port, tls_context=_tls_context)
    scheme = 'https' if options.tls else 'http'
    shown = 'localhost' if options.host in ('0.0.0.0', '', '::') else options.host
    address = '%s://%s:%d/' % (scheme, shown, server.server_port)
    print('Local mirror: %s (Ctrl+C to stop)' % address, flush=True)
    print('  serving %s' % ROOT, flush=True)
    if options.host not in ('127.0.0.1', '::1'):
        print('  Other devices must use %s://<this machines address>:%d/ - and only https or localhost keeps\n'
              '  WebGPU and shared memory enabled, so use --tls for LAN play.' % (scheme, server.server_port), flush=True)
    if options.open:
        open_browser_later(address + '__support/')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
