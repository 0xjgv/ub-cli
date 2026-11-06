#!/bin/sh
# Install or update ub from a checksummed GitHub release.
# UB_VERSION        release version, e.g. v0.1.0 (default: latest)
# UB_INSTALL_DIR    executable directory (default: $HOME/.local/bin)
# UB_DOWNLOAD_BASE  release base URL or absolute directory for local testing
set -eu

REPO=0xjgv/ub-cli

die() {
    echo "ub install: $*" >&2
    exit 1
}

fetch() {
    case $1 in
    /*) cp "$1" "$2" ;;
    *)
        if command -v curl >/dev/null 2>&1; then
            curl -fsSL "$1" -o "$2"
        elif command -v wget >/dev/null 2>&1; then
            wget -q -O "$2" "$1"
        else
            die "curl or wget is required"
        fi
        ;;
    esac || die "download failed: $1"
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        die "sha256sum or shasum is required"
    fi
}

case $(uname -s) in
Darwin) os=darwin ;;
Linux) os=linux ;;
*) die "unsupported OS: $(uname -s)" ;;
esac
case $(uname -m) in
x86_64 | amd64) arch=amd64 ;;
aarch64 | arm64) arch=arm64 ;;
*) die "unsupported architecture: $(uname -m)" ;;
esac
if [ "$os" = darwin ] && [ "$arch" = amd64 ] &&
    [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then
    arch=arm64
fi

base=${UB_DOWNLOAD_BASE:-https://github.com/$REPO/releases}
base=${base%/}
version=${UB_VERSION:-}
version=${version#v}
if [ -n "$version" ]; then
    printf '%s\n' "$version" | awk '/^[0-9]+\.[0-9]+\.[0-9]+$/ {valid=1} END {exit !valid}' ||
        die "UB_VERSION must be a version such as v0.1.0"
    release=$base/download/v$version
else
    release=$base/latest/download
fi

install_dir=${UB_INSTALL_DIR:-$HOME/.local/bin}
target=$install_dir/ub
[ ! -d "$target" ] || die "$target is a directory"
tmpdir=$(mktemp -d)
tmpbin=
trap 'rm -rf "$tmpdir"; [ -z "$tmpbin" ] || rm -f "$tmpbin"' 0
trap 'exit 1' HUP INT TERM

fetch "$release/checksums.txt" "$tmpdir/checksums.txt"
archive=$(awk -v os="$os" -v arch="$arch" -v version="$version" '
    {sub(/^\*/, "", $2)}
    $2 ~ "^ub_[0-9]+[.][0-9]+[.][0-9]+_" os "_" arch "[.]tar[.]gz$" &&
        (version == "" || $2 == "ub_" version "_" os "_" arch ".tar.gz") {
        found=$2; count++
    }
    END {if (count == 1) print found}
' "$tmpdir/checksums.txt")
[ -n "$archive" ] || die "expected one $os/$arch archive in $release/checksums.txt"
expected=$(awk -v name="$archive" '{sub(/^\*/, "", $2)} $2 == name {print $1}' "$tmpdir/checksums.txt")
fetch "$release/$archive" "$tmpdir/$archive"
actual=$(sha256 "$tmpdir/$archive")
[ "$actual" = "$expected" ] || die "checksum mismatch for $archive"
tar -xzf "$tmpdir/$archive" -C "$tmpdir" ub || die "cannot extract ub from $archive"
[ -f "$tmpdir/ub" ] && [ ! -L "$tmpdir/ub" ] || die "archive must contain a regular ub executable"

previous=
if [ -x "$target" ]; then
    previous=$("$target" --version 2>/dev/null || true)
fi
mkdir -p "$install_dir"
tmpbin=$(mktemp "$install_dir/.ub.XXXXXX")
cp "$tmpdir/ub" "$tmpbin"
chmod 755 "$tmpbin"
current=$("$tmpbin" --version) || die "downloaded ub does not run"
archive_version=${archive#ub_}
archive_version=${archive_version%_"$os"_"$arch".tar.gz}
[ "$current" = "ub $archive_version" ] || die "downloaded ub reported an unexpected version: $current"
mv -f "$tmpbin" "$target"
tmpbin=

if [ -n "$previous" ] && [ "$previous" != "$current" ]; then
    echo "Updated $target: $previous -> $current"
else
    echo "Installed $target: $current"
fi
case :$PATH: in
*":$install_dir:"*) ;;
*) echo "Add $install_dir to PATH: export PATH=\"$install_dir:\$PATH\"" ;;
esac
