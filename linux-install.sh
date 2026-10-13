#!/bin/sh
# Pemasang sekali-perintah untuk Linux. Salin-tempel ini di terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/denllowss/playgta5/main/linux-install.sh | sh
#
# Ia menyiapkan folder game di ~/playgta5 (atau TARGET=/mnt/hdd2/playgta5 sebelum perintah),
# lalu membuka menu 1/2/3 dari linux-launcher.sh. Tidak ada yang dipasang ke sistem
# (tidak ada sudo) kecuali Anda memilih menu 10.
#
# Variant environment: TARGET, REPO, BRANCH, PYTHON.
set -eu

TARGET=${TARGET:-"$HOME/playgta5"}
REPO=${REPO:-https://github.com/denllowss/playgta5.git}
BRANCH=${BRANCH:-main}

say()  { printf '  %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# dijalankan dari dalam repo yang sudah ada? langsung ke menunya
if [ -f "$(CDPATH= cd -- "$(dirname -- "$0" 2>/dev/null)" 2>/dev/null && pwd -P)/linux-launcher.sh" ] \
        && [ -f "$(CDPATH= cd -- "$(dirname -- "$0" 2>/dev/null)" 2>/dev/null && pwd -P)/serve_local.py" ]; then
    here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
    exec sh "$here/linux-launcher.sh" menu
fi

say "playgta5 dipasang di: $TARGET"
mkdir -p "$TARGET" || { say "tidak bisa membuat $TARGET (TARGET=/path/lain untuk mengganti)"; exit 1; }

if [ -f "$TARGET/linux-launcher.sh" ]; then
    say "sudah ada repository di sana; tidak diunduh ulang"
elif have git; then
    say "mengambil repository dengan git ..."
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$TARGET"
else
    say "git tidak ditemukan; memakai arsip ZIP"
    temp=$(mktemp -d)
    url="https://codeload.github.com/denllowss/playgta5/zip/refs/heads/$BRANCH"
    if have curl; then
        curl -fsSL --retry 3 -o "$temp/repo.zip" "$url" || { rm -rf "$temp"; exit 1; }
    elif have wget; then
        wget -q -O "$temp/repo.zip" "$url" || { rm -rf "$temp"; exit 1; }
    else
        say "butuh git, curl, atau wget untuk mengambil repository"; rm -rf "$temp"; exit 1
    fi
    if have unzip; then
        unzip -q "$temp/repo.zip" -d "$temp"
    elif have python3; then
        python3 -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$temp/repo.zip" "$temp"
    else
        say "butuh unzip atau python3 untuk membuka arsip"; rm -rf "$temp"; exit 1
    fi
    mkdir -p "$TARGET"
    # the ZIP wraps one folder named playgta5-<branch>; move its contents up
    inner=$(find "$temp" -mindepth 1 -maxdepth 1 -type d | head -1)
    cp -R "$inner"/. "$TARGET"/
    rm -rf "$temp"
fi

chmod +x "$TARGET"/*.sh "$TARGET"/*.command 2>/dev/null || true
say "selesai. membuka menu ..."
printf '\n'
exec sh "$TARGET/linux-launcher.sh" menu
