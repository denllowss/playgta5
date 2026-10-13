#!/bin/sh
# playgta5 - pemasang & peluncur untuk Linux (Ubuntu, Debian, Fedora, Arch, openSUSE, WSL2, SteamOS).
# Jalankan tanpa argumen untuk menu 1/2/3; jalur Windows (.cmd) dan macOS (.command) tidak diubah.
#
#   ./linux-launcher.sh                      buka menu interaktif
#   sh linux-launcher.sh                     sama, walau bit executable hilang
#
# Perintah langsung (tanpa menu, untuk skrip/SSH):
#   check        lapor Python, GPU, disk, filesystem, dan status file game
#   download     unduh + verifikasi ±21 GB dari GitHub Release (bisa disambung kalau putus)
#   verify       SHA-256 seluruh file game yang terpasang
#   launch       server http://localhost:8000/ lalu buka halaman cek browser
#   lan          server https://0.0.0.0:8000/ supaya bisa dimainkan dari HP/PC lain
#   background|status|stop|log               server tanpa terminal yang harus dibiarkan terbuka
#   service      install|remove auto-start via systemd user
#   deps         pasang dependency yang kurang (apt/dnf/pacman/zypper)
#   config       atur lokasi folder game (disk kedua) dan port, disimpan di playgta5-linux.conf
#   test         unit test repository ini
#   desktop      entri menu aplikasi (GNOME/KDE) supaya bisa diklik dua kali
#
# Environment: PYTHON, GAME_ROOT, PORT, PIDFILE, LOGFILE. Konfig tersimpan di playgta5-linux.conf.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$here"

CONF="$here/playgta5-linux.conf"
if [ -f "$CONF" ]; then
    conf_root=$(sed -n 's/^GAME_ROOT=//p' "$CONF" | tail -1)
    conf_port=$(sed -n 's/^PORT=//p' "$CONF" | tail -1)
    [ -n "$conf_root" ] && GAME_ROOT=${GAME_ROOT:-$conf_root}
    [ -n "$conf_port" ] && PORT=${PORT:-$conf_port}
fi

PYTHON=${PYTHON:-python3}
PORT=${PORT:-8000}
GAME_ROOT=${GAME_ROOT:-}
RUN_DIR="$here/.local-run"
PIDFILE=${PIDFILE:-"$RUN_DIR/server.pid"}
LOGFILE=${LOGFILE:-"$RUN_DIR/server.log"}
MIN_MINOR=11

say()  { printf '  %s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# menu boleh dipanggil lewat pipe (curl | sh): pakai /dev/tty bila ada, kalau tidak stdin
if [ -t 0 ] || [ -t 1 ]; then MENU_IN=/dev/tty; else MENU_IN=/dev/stdin; fi

python_ok() {
    have "$PYTHON" || return 1
    "$PYTHON" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, $MIN_MINOR) else 1)" 2>/dev/null
}

need_python() {
    python_ok && return 0
    have "$PYTHON" && version=$(  "$PYTHON" -V 2>&1) || version="belum terpasang"
    cat >&2 <<EOF
Perlunya Python 3.$MIN_MINOR atau lebih baru (terdeteksi: $version).

    Ubuntu 24.04 / Debian 13 / Fedora 40+ / Arch : python3 sudah cukup baru
    Ubuntu 22.04 / 20.04 : sudo add-apt-repository ppa:deadsnakes/ppa
                           sudo apt install python3.11
                           PYTHON=python3.11 ./linux-launcher.sh launch
    pilih otomatis         ./linux-launcher.sh deps

EOF
    return 1
}

mirror_dir() {
    for candidate in "${GAME_ROOT:-$here}/mirror/playgta5.com" "$here/mirror/playgta5.com" "${GAME_ROOT:-$here}"; do
        [ -f "$candidate/index.html" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    return 1
}

target_root() { printf '%s\n' "${GAME_ROOT:-$here}"; }

root_flag() { [ -n "$GAME_ROOT" ] && printf '%s\n' "$GAME_ROOT"; }

do_check() {
    printf 'python\n'
    if python_ok; then
        say "$PYTHON: $(  "$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') (butuh 3.$MIN_MINOR+)"
    else
        say "$PYTHON: terlalu tua / belum ada -> ./linux-launcher.sh deps"
    fi
    printf '\ndownloader\n'
    say "curl    : $(have curl && echo ya || echo tidak)"
    say "wget    : $(have wget && echo ya || echo tidak)  (salah satu sudah cukup)"
    say "openssl : $(have openssl && echo ya || echo 'tidak - hanya perlu untuk mode lan')"
    printf '\ndisplay & browser\n'
    say "display : ${DISPLAY:-${WAYLAND_DISPLAY:-tidak ada (headless: pakai background / lan / ssh -L)}}"
    say "xdg-open: $(have xdg-open && echo ya || echo 'tidak - salin URL-nya ke Chrome/Edge')"
    for browser in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge; do
        have "$browser" && say "browser : $browser" && break
    done
    printf '\ngrafis (WebGPU butuh driver Vulkan)\n'
    if have lspci; then
        say "GPU     : $(lspci 2>/dev/null | grep -Ei 'vga|3d|display' | head -1 | cut -c1-100)"
    else
        say "GPU     : install pciutils (sudo apt install pciutils) supaya terdeteksi"
    fi
    have vulkaninfo && say "Vulkan  : $(vulkaninfo --summary 2>/dev/null | grep -m1 'deviceName' | cut -c1-90 || echo 'vulkan-tools ada, perangkat tidak dilaporkan')"
    say "driver  : Mesa 23+ (AMD/Intel) atau NVIDIA 535+; kalau Chrome tidak punya WebGPU buka chrome://gpu"
    printf '\nruang disk untuk ±25 GB\n'
    root=$(target_root)
    probe=$root
    while [ ! -d "$probe" ] && [ "$probe" != / ]; do probe=$(dirname "$probe"); done
    [ "$probe" = "$root" ] || say "catatan : $root belum ada; diukur di $probe"
    say "target  : $root"
    if have df; then
        free=$(df -h --output=avail "$probe" 2>/dev/null | tail -1 | tr -s ' ' || true)
        [ -n "$free" ] || free=$(df -h "$probe" 2>/dev/null | tail -1 || true)
        say "sisa    : $free"
        fstype=$(df -PT "$probe" 2>/dev/null | tail -1 | awk '{print $2}')
        say "fs      : ${fstype:-tidak diketahui}"
        say "inodes  : $(df -i "$probe" 2>/dev/null | tail -1 | awk '{print $4}') tersedia (butuh 12.482 file)"
        case "$fstype" in
            vfat|exfat|msdos|ntfs|"fuse drvfs"|9p|cifs|smb|sshfs)
                say "AWAS    : filesystem itu tidak kuat menyimpan semua nama file game dan"
                say "          membatasi satu file di 4 GB. Pakai ext4/xfs/btrfs." ;;
        esac
    fi
    printf '\nfile game\n'
    if found=$(mirror_dir); then
        say "mirror  : $found"
        say "isi     : $(ls "$found" 2>/dev/null | tr '\n' ' ')"
    else
        say "mirror  : belum ada -> menu pilihan 2 (download)"
    fi
}

do_deps() {
    os_id=unknown
    [ -r /etc/os-release ] && os_id=$(. /etc/os-release 2>/dev/null && printf '%s\n' "${ID:-unknown}")
    missing=
    python_ok || missing="$missing python3.11+"
    have curl || have wget || missing="$missing curl"
    have git || missing="$missing git"
    say "distro  : $os_id"
    say "kurang  : ${missing:-tidak ada, semua siap}"
    [ -n "$missing" ] || return 0
    case "$os_id" in
        ubuntu|debian|linuxmint|pop) cmd="sudo apt update && sudo apt install -y python3 python3-venv curl git openssl pciutils" ;;
        fedora|rhel|centos|rocky)    cmd="sudo dnf install -y python3 curl git openssl pciutils" ;;
        arch|manjaro|endeavouros)    cmd="sudo pacman -S --needed python curl git openssl pciutils" ;;
        opensuse-tumbleweed|opensuse-leap|sles) cmd="sudo zypper install -y python3 curl git openssl pciutils" ;;
        clear-linux-os)              cmd="sudo swupd bundle-add dev-utils" ;;
        alpine)                      cmd="sudo apk add python3 curl git openssl" ;;
        *)                           cmd="sudo <pkg manager> install python3 curl git openssl" ;;
    esac
    printf '\nperintah yang akan dijalankan:\n  %s\n' "$cmd"
    printf 'jalankan sekarang? [y/N] '
    answer=
    if read -r answer < "$MENU_IN" 2>/dev/null; then :; else answer=n; fi
    case "$answer" in
        y|Y|yes|YES) ;;
        *) say "dibatalkan - jalankan sendiri perintah di atas"; return 0 ;;
    esac
    if have sudo; then
        exec sh -c "$cmd"
    fi
    say "sudo tidak ada di sistem ini; jalankan perintah di atas sebagai root"
    return 1
}

do_config() {
    printf 'folder untuk game (Enter = %s): ' "$(target_root)"
    read -r new_root < "$MENU_IN" 2>/dev/null || new_root=
    printf 'port server (Enter = %s): ' "$PORT"
    read -r new_port < "$MENU_IN" 2>/dev/null || new_port=
    {
        [ -n "$new_root" ] && printf 'GAME_ROOT=%s\n' "$new_root"
        [ -n "$new_port" ] && printf 'PORT=%s\n' "$new_port"
    } > "$CONF" || fail "tidak bisa menulis $CONF"
    [ -n "$new_root" ] && GAME_ROOT=$new_root
    [ -n "$new_port" ] && PORT=$new_port
    say "tersimpan di $CONF"
    say "GAME_ROOT=$GAME_ROOT"
    say "PORT=$PORT"
    say "hapus file itu untuk kembali ke bawaan"
}

do_download() {
    need_python
    root=$(target_root)
    [ "$root" = "$here" ] || say "target   : $root"
    say "±21 GB dari GitHub Release; arsip yang sudah selesai tidak diunduh ulang."
    say "ruang yang dibutuhkan sekitar 25 GB (arsip dihapus setelah diekstrak)."
    if [ $# -eq 0 ]; then
        printf 'lanjutkan download? [y/N] '
        answer=
        if read -r answer < "$MENU_IN" 2>/dev/null; then :; else answer=y; fi
        case "$answer" in y|Y|yes|YES) : ;; *) say "dibatalkan"; return 0 ;; esac
    fi
    if [ "$root" = "$here" ]; then
        exec "$PYTHON" "$here/download_game.py"
    fi
    exec "$PYTHON" "$here/download_game.py" --root "$root"
}

do_launch() {
    need_python
    if [ -n "$GAME_ROOT" ]; then
        exec "$PYTHON" "$here/serve_local.py" --port "$PORT" --open --root "$GAME_ROOT" "$@"
    fi
    exec "$PYTHON" "$here/serve_local.py" --port "$PORT" --open "$@"
}

do_lan() {
    need_python
    printf 'Perangkat lain membuka https://<alamat-mesin-ini>:%s/ - terima peringatan sertifikat self-signed satu kali.\n' "$PORT"
    printf 'Firewall: sudo ufw allow %s/tcp   (atau hentikan dulu untuk tes)\n' "$PORT"
    if [ -n "$GAME_ROOT" ]; then
        exec "$PYTHON" "$here/serve_local.py" --port "$PORT" --host 0.0.0.0 --tls --open --root "$GAME_ROOT" "$@"
    fi
    exec "$PYTHON" "$here/serve_local.py" --port "$PORT" --host 0.0.0.0 --tls --open "$@"
}

port_shown() {
    previous=
    port_shown=$PORT
    for argument in "$@"; do
        [ "$previous" = "--port" ] && port_shown=$argument
        previous=$argument
    done
}

do_background() {
    need_python
    port_shown "$@"
    mkdir -p "$RUN_DIR"
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        fail "sudah jalan (pid $(cat "$PIDFILE")); hentikan dengan: ./linux-launcher.sh stop"
    fi
    if [ -n "$GAME_ROOT" ]; then set -- --root "$GAME_ROOT" "$@"; fi
    nohup "$PYTHON" "$here/serve_local.py" --port "$PORT" --quiet "$@" >"$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    kill -0 "$(cat "$PIDFILE")" 2>/dev/null || { cat "$LOGFILE" >&2; fail "server langsung mati; lihat $LOGFILE"; }
    say "jalan di latar belakang (pid $(cat "$PIDFILE")); log: $LOGFILE"
    say "buka http://localhost:$port_shown/__support/ di Chrome atau Edge"
}

do_stop() {
    [ -f "$PIDFILE" ] || { echo "tidak ada server latar belakang."; exit 0; }
    pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null; then kill "$pid"; say "pid $pid dihentikan"; else say "pid $pid sudah mati"; fi
    rm -f "$PIDFILE"
}

do_status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        say "server latar belakang: jalan (pid $(cat "$PIDFILE")), log $LOGFILE"
        have ss && ss -ltn 2>/dev/null | grep ":$PORT " | sed 's/^/  /'
    else
        say "server latar belakang: tidak jalan"
    fi
    if found=$(mirror_dir); then say "file game: $found"; else say "file game: belum ada (menu 2 untuk download)"; fi
}

do_test() {
    need_python
    exec "$PYTHON" -m unittest discover -s tests -v "$@"
}

do_verify() {
    need_python
    say "membaca ±21 GB untuk memeriksa SHA-256; butuh beberapa menit."
    exec "$PYTHON" "$here/verify_game.py" "$@"
}

do_desktop() {
    dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$dir"
    {
        echo '[Desktop Entry]'
        echo 'Type=Application'
        echo 'Version=1.0'
        echo 'Name=playgta5 (Linux)'
        echo 'Comment=Pasang dan mainkan mirror playgta5 di komputer ini'
        echo "Exec=$here/linux-launcher.sh menu"
        echo "Path=$here"
        echo 'Terminal=true'
        echo 'Categories=Game;'
    } > "$dir/playgta5-local.desktop"
    chmod +x "$dir/playgta5-local.desktop"
    have update-desktop-database && update-desktop-database "$dir" || true
    say "entri menu ditulis: $dir/playgta5-local.desktop"
}

do_service() {
    action=${1:-install}
    case "$action" in
        install)
            need_python
            have systemctl || fail "tidak ada systemctl di sini (WSL1/container): pakai ./linux-launcher.sh background"
            dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
            mkdir -p "$dir"
            extra=
            [ -n "$GAME_ROOT" ] && extra="--root $GAME_ROOT"
            {
                echo '[Unit]'
                echo "Description=playgta5 local game server (http://localhost:$PORT/)"
                echo 'After=network.target'
                echo
                echo '[Service]'
                echo 'Type=simple'
                echo "WorkingDirectory=$here"
                echo "ExecStart=$(command -v "$PYTHON") $here/serve_local.py --port $PORT --quiet $extra"
                echo "Environment=PLAYGTA5_MIRROR=$here/mirror/playgta5.com"
                echo 'Restart=on-failure'
                echo 'RestartSec=2'
                echo 'Nice=5'
                echo
                echo '[Install]'
                echo 'WantedBy=default.target'
            } > "$dir/playgta5-local.service"
            if ! systemctl --user daemon-reload 2>/dev/null; then
                fail "tidak ada systemd user session (umum di WSL, su, container).
    Unit ditulis ke $dir/playgta5-local.service.
    WSL: aktifkan systemd di /etc/wsl.conf ([boot] systemd=true) lalu restart,
    atau cukup pakai: ./linux-launcher.sh background"
            fi
            systemctl --user enable --now playgta5-local.service
            have loginctl && loginctl enable-linger "$USER" || true
            say "auto-start aktif; cek: systemctl --user status playgta5-local"
            ;;
        remove)
            have systemctl || fail "tidak ada systemctl di sini"
            systemctl --user disable --now playgta5-local.service 2>/dev/null || true
            rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/playgta5-local.service"
            systemctl --user daemon-reload 2>/dev/null || true
            say "user service dihapus"
            ;;
        *) fail "pemakaian: $0 service install|remove" ;;
    esac
}

menu_status() {
    if python_ok; then status_python="$PYTHON siaga"; else status_python="PYTHON TIDAK ADA (menu 10)"; fi
    if found=$(mirror_dir); then status_game="siap"; else status_game="belum ada -> menu 2"; fi
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then status_server="jalan (pid $(cat "$PIDFILE"))"; else status_server="mati"; fi
    say "$status_python | file game: $status_game | server: $status_server | port $PORT"
    say "folder: $here"
}

do_menu() {
    while :; do
        printf '\n=========================================\n'
        printf ' playgta5 - pemasang Linux\n'
        printf '=========================================\n'
        menu_status
        printf '\n'
        printf '  1) Cek sistem & yang kurang\n'
        printf '  2) Download game (±21 GB, bisa lanjut kalau putus)\n'
        printf '  3) Verifikasi file game (SHA-256)\n'
        printf '  4) Main sekarang (http://localhost:%s/)\n' "$PORT"
        printf '  5) Main dari HP / PC lain (LAN + HTTPS)\n'
        printf '  6) Main di latar belakang (terminal boleh ditutup)\n'
        printf '  7) Status server\n'
        printf '  8) Berhentikan server\n'
        printf '  9) Auto-start setiap login (systemd)\n'
        printf ' 10) Pasang yang kurang (python, curl, git)\n'
        printf ' 11) Ganti folder game / port\n'
        printf ' 12) Jalankan test repository\n'
        printf ' 13) Entri menu desktop\n'
        printf '  0) Keluar\n\n'
        printf 'pilih [0-13]: '
        choice=
        read -r choice < "$MENU_IN" 2>/dev/null || { printf '\n(bukan terminal: pakai perintah langsung, contoh: sh linux-launcher.sh download)\n'; return 0; }
        skip_pause=0
        case "$choice" in
            1) ( do_check ) ;;
            2) ( do_download ); skip_pause=1 ;;       # already asked y/N from this keyboard
            3) ( do_verify ) ;;
            4) ( do_launch ) ;;
            5) ( do_lan ) ;;
            6) ( do_background ) ;;
            7) ( do_status ) ;;
            8) ( do_stop ) ;;
            9) ( do_service install ) ;;
            10) ( do_deps ); skip_pause=1 ;;
            11) do_config; skip_pause=1 ;;            # no subshell: the new folder/port apply at once
            12) ( do_test ) ;;
            13) ( do_desktop ) ;;
            0|q|Q|exit) printf 'sampai jumpa.\n'; return 0 ;;
            *) say "pilihan tidak dikenal: $choice" ;;
        esac
        if [ "$skip_pause" = 1 ]; then
            printf '\n'
            continue
        fi
        printf '\nTekan Enter untuk kembali ke menu. '
        read -r _ < "$MENU_IN" 2>/dev/null || return 0
    done
}

command=${1:-}
if [ -z "$command" ]; then
    if [ -t 0 ] || [ -t 1 ]; then command=menu; else command=check; fi
else
    shift
fi
case "$command" in
    check)      do_check ;;
    download)   do_download "$@" ;;
    verify)     do_verify "$@" ;;
    launch)     do_launch "$@" ;;
    lan)        do_lan "$@" ;;
    background) do_background "$@" ;;
    stop)       do_stop ;;
    status)     do_status ;;
    log)        mkdir -p "$RUN_DIR"; [ -f "$LOGFILE" ] && exec tail -n 60 -f "$LOGFILE" || echo "belum ada log di $LOGFILE" ;;
    test|selftest) do_test "$@" ;;
    desktop)    do_desktop ;;
    service)    do_service "$@" ;;
    deps)       do_deps ;;
    config)     do_config ;;
    menu)       do_menu ;;
    help|-h|--help) awk 'NR==1{next} /^#/{sub(/^# ?/, ""); print; next} {exit}' "$0" ;;
    *) fail "perintah tidak dikenal: '$command' (lihat: ./linux-launcher.sh help)" ;;
esac
