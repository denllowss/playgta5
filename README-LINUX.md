# playgta5 di Ubuntu / Linux

Berbeda dengan Windows (`.cmd` + `runtime\python.exe`) dan macOS (`.command`), di Linux
tidak ada yang perlu di-bundle: `python3` bawaan distro sudah cukup, dan repo ini sudah
menyediakan seluruh data gamenya lewat GitHub Release (14 arsip, ±21 GB) sehingga
`download_game.py` tinggal mengunduhnya. Yang ditambahkan di sini hanya jalur masuk
Linux yang nyaman: `linux-launcher.sh`.

Jalur Windows dan macOS tidak diubah sama sekali.

## 0. Prasyarat

| Butuh | Keterangan |
| --- | --- |
| Python 3.11+ | `hashlib.file_digest` dipakai verifier. Ubuntu 24.04 (3.12), Debian 13, Fedora 40+, Arch sudah aman. Ubuntu 22.04/20.04: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.11` lalu jalankan dengan `PYTHON=python3.11 ./linux-launcher.sh …` |
| curl **atau** wget | hanya untuk mengunduh release |
| Chrome / Chromium / Edge terbaru | WebGPU + WebAssembly **Memory64**. Di Linux, WebGPU jalan lewat Vulkan: Mesa 23+ (AMD/Intel) atau driver NVIDIA 535+. Firefox baru menyalakan WebGPU secara default di Windows/macOS, jadi kalau memakai Firefox Linux set `dom.webgpu.enabled=true` |
| ±25 GB ruang kosong | di filesystem ext4/xfs/btrfs, dan RAM 16 GB ke atas disarankan |

Cek semuanya sekali jalan:

```bash
./linux-launcher.sh check
```

Skrip ini melaporkan versi Python, downloader, GPU/driver, apakah display tersedia,
ruang + jenis filesystem + inode di target instalasi, dan status folder `mirror`.

## 1. Instalasi (satu perintah, lalu pilih di menu)

```bash
curl -fsSL https://raw.githubusercontent.com/denllowss/playgta5/main/linux-install.sh | sh
```

Perintah itu mengambil repository ke `~/playgta5` lalu membuka menunya:

```text
  1) Cek sistem & yang kurang                 7) Status server
  2) Download game (±21 GB)                   8) Berhentikan server
  3) Verifikasi file game                     9) Auto-start setiap login
  4) Main sekarang                            10) Pasang yang kurang (python, curl, git)
  5) Main dari HP / PC lain (LAN + HTTPS)     11) Ganti folder game / port
  6) Main di latar belakang                   12) Jalankan test repository
                                              13) Entri menu desktop
```

Alurnya cukup **1 → 2 → 4**: cek mesin, download gamenya, lalu main. Menu 3 dan 12 opsional
(verifikasi SHA-256 dan test repo), menu 10 memasang paket yang kurang, menu 11 memindahkan
folder game ke disk kedua.

Suka mengetik perintah (atau lewat SSH/skrip)? Semua aksi punya nama perintah dalam bahasa
Inggris:

```bash
git clone https://github.com/denllowss/playgta5.git
cd playgta5
./linux-launcher.sh check
./linux-launcher.sh download      # ±21 GB, bisa disambung kalau putus
./linux-launcher.sh verify        # SHA-256 semua 12.482 file game
./linux-launcher.sh launch        # http://localhost:8000/__support/
```

Tidak bisa menjalankan `./linux-launcher.sh` (bit executable hilang karena ZIP diekstrak
tanpa `unzip`)? Pakai `sh linux-launcher.sh download`, lalu `chmod +x *.sh *.command`.

`launch` membuka browser bila ada display; di sesi headless (server/WSL/SSH) ia hanya
mencetak URL-nya, tidak crash. Buka alamat itu di Chrome/Edge di mesin Anda.
Lewat SSH, teruskan port-nya: `ssh -L 8000:localhost:8000 user@server`.

Kalau game-nya macet, mulai dengan profil ringan seperti anjuran README utama:
`http://localhost:8000/?low=1&fps=30&res=1280x720`.

## 2. Game di disk lain

`mirror` tidak harus berada di dalam folder repo:

```bash
GAME_ROOT=/mnt/hdd2/playgta5 ./linux-launcher.sh download    # unduh + ekstrak ke sana
./linux-launcher.sh launch --root /mnt/hdd2/playgta5         # atau:
export PLAYGTA5_MIRROR=/mnt/hdd2/playgta5/mirror/playgta5.com
```

Alternatif lain: buat symlink `mirror` di dalam repo ke folder tersebut.

Peringatan filesystem: vfat/exfat (USB stick, partisi Windows lama) tidak bisa menyimpan
sebagian nama file game dan membatasi satu file di 4 GB; mountan `/mnt/d` di WSL (DrvFs/9P)
juga jauh lebih lambat untuk 12 ribu file kecil — simpan mirror di ext4 dalam WSL.

## 3. Headless, latar belakang, systemd

```bash
./linux-launcher.sh background          # nohup + pidfile, log di .local-run/server.log
./linux-launcher.sh status
./linux-launcher.sh log                 # tail -f
./linux-launcher.sh stop

./linux-launcher.sh service install     # systemd user service, jalan sejak login
./linux-launcher.sh service remove
```

Tanpa session systemd (WSL1, container, `su`), `service install` berhenti dengan pesan
yang menjelaskan pilihan `background`. Entri menu desktop: `./linux-launcher.sh desktop`.

## 4. Dimainkan di perangkat lain (HP / TV / PC lain)

```bash
./linux-launcher.sh lan
```

Ini menjalankan `serve_local.py --host 0.0.0.0 --tls`: sertifikat self-signed dibuat
sekali di `.local-tls/` (dibuat ulang otomatis kalau IP mesin berubah), karena browser
hanya mengaktifkan `SharedArrayBuffer`/WebGPU pada *secure context* — `http://<ip>:8000`
tidak akan cukup, sedangkan `https://<ip>:8000` dengan sertifikat yang Anda terima ya.
Buka `https://<alamat-mesin>:8000/__support/` dari perangkat itu dan periksa laporannya
terlebih dulu. Kalau ada firewall: `sudo ufw allow 8000/tcp`.

## 5. Perintah & opsi

```
./linux-launcher.sh                       # tanpa argumen = menu 1/2/3 (kalau ada terminal)
./linux-launcher.sh menu | check | download | verify | launch | lan
                      | background | stop | status | log | deps | config
                      | test | desktop | service install|remove

curl -fsSL https://raw.githubusercontent.com/denllowss/playgta5/main/linux-install.sh | sh
  -> ambil repository ke ~/playgta5 (TARGET=/mnt/hdd2/playgta5 untuk disk lain) lalu buka menu

  PORT=8081 ./linux-launcher.sh launch        ganti port
  GAME_ROOT=/mnt/hdd2/playgta5 ./linux-launcher.sh download
  PYTHON=python3.11 ./linux-launcher.sh …     pilih interpreter

python3 download_game.py [--root DIR] [--downloader auto|curl|wget] [--keep-archives]
                         [--manifest F] [--inventory F]
python3 serve_local.py   [--root DIR] [--port 8000] [--host 127.0.0.1] [--open]
                         [--tls] [--certs DIR] [--quiet] [--check] [--no-case-insensitive]
python3 verify_game.py   [--root DIR] [--all]
```

`verify_game.py --all` juga memeriksa helper lama terhadap inventory rilis, sehingga
akan melaporkan perbedaan yang memang diharapkan setelah pembaruan.

## 6. Yang berubah di kode bersama (bukan hanya Linux)

* `serve_local.py` — folder mirror bisa dari `--root`/`$PLAYGTA5_MIRROR`; HTTP/1.1
  keep-alive (mesin game membuka ribuan request kecil, body request yang ditolak kini
  selalu dibaca supaya koneksi tidak kacau); fallback nama file beda huruf besar/kecil
  untuk mirror yang dipindah dari NTFS; `--host`/`--tls`; `--check`; `webbrowser` aman
  di sesi tanpa display; `Handler` menerima argumen `directory` sehingga mudah diuji.
* `download_game.py` — `--root` (instal di disk lain), wget sebagai cadangan curl,
  `--keep-archives`, dan pesan akhir yang menyebut launcher Linux.
* `build_installer.py` — `PlayGTA5-Installer.zip` kini ikut membawa `linux-launcher.sh`
  dan file ini, dengan bit executable.
* `tests/test_posix_launcher.py` — test baru: launcher harus valid untuk `/bin/sh`
  (Ubuntu memakai dash), LF saja, membawa perintah yang dijanjikan; bundle installer
  harus memuat launcher Linux dengan bit executable; fallback case, sinkronisasi
  keep-alive, `--root`/env resolution, `--check`, headless open, dan
  `download_game.install()` diuji end-to-end lewat HTTP sungguhan (termasuk
  melanjutkan file `.partial`, arsip rusak ditolak, helper tidak tertimpa).

Jalankan sendiri: `./linux-launcher.sh test` atau `python3 -m unittest discover -s tests -v`.

## 7. Troubleshooting

| Gejala | Cek |
| --- | --- |
| `Game files are missing in …` | `download` belum selesai; `./linux-launcher.sh verify` lalu ulangi `download` (arsip yang sudah terverifikasi tidak diunduh ulang) |
| Port 8000 dipakai | `PORT=8001 ./linux-launcher.sh launch`, atau `./linux-launcher.sh stop` kalau server lama masih di latar belakang |
| Loading berhenti, halaman bilang "not cross-origin isolated" | halaman dibuka lewat alamat yang bukan localhost/https — pakai `launch` (localhost) atau `lan` (https) |
| WebGPU tidak tersedia | `chrome://gpu`; aktifkan percepatan grafis di `chrome://settings/system`; perbarui driver. Lalu muat ulang |
| Tab crash / `memory access out of bounds` | itu error memori WebAssembly, bukan bukti RAM kurang. Catat error merah pertama + salin laporan dari `/__support/` |
| 404 pada sebagian file di mirror hasil salinan | server sudah mencoba fallback beda kapital; kalau tetap 404, mirror tidak lengkap: jalankan `verify` |
| `Download complete` tapi `verify` gagal | arsip rilis berbeda dari inventory; unduh ulang bagian yang disebut (lihat `.installed-parts/`) |
| WSL sangat lambat | pindahkan mirror ke ext4 WSL (`\\wsl$`), jangan `/mnt/c` |

Status dukungan: seluruh test repo (termasuk yang baru) lulus di Linux, dan server
menyajikan/melayani byte-range serta batch I/O dengan benar. **Gameplay di Linux belum
divalidasi pada perangkat keras** — hal yang sama juga masih berlaku untuk macOS di repo
ini, jadi kalau dunia render-nya bermasalah, laporkan dengan data dari `/__support/`.
