#!/bin/sh
# Build the current platform's standalone executable and release archive.
set -eu

root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
uv=${UV:-uv}
python_version=${PYTHON_VERSION:-3.11}
case $(uname -s) in
Darwin) os=darwin ;;
Linux) os=linux ;;
*) echo "release: unsupported OS" >&2; exit 1 ;;
esac
case $(uname -m) in
x86_64|amd64) arch=amd64 ;;
aarch64|arm64) arch=arm64 ;;
*) echo "release: unsupported architecture" >&2; exit 1 ;;
esac

stage=$(mktemp -d)
archive_tmp=
trap 'rm -rf "$stage"; [ -z "$archive_tmp" ] || rm -f "$archive_tmp"' EXIT
trap 'exit 1' HUP INT TERM
"$uv" venv --python "$python_version" "$stage/venv"
python="$stage/venv/bin/python"
"$uv" pip install --python "$python" . 'pyinstaller==6.16.0'
version=$("$python" -c 'from importlib.metadata import version; print(version("ub-cli"))')
mkdir -p "$stage/bin"
cp LICENSE "$stage/bin/LICENSE"
"$python" scripts/collect_notices.py "$stage/bin"

# Download the pinned language pack's parsers into an isolated cache.
"$python" - "$stage/grammar-cache" <<'PY'
import shutil
import sys
from pathlib import Path
import tree_sitter_language_pack as languages
from scripts.collect_notices import GRAMMARS

languages.configure(languages.PackConfig(cache_dir=sys.argv[1]))
languages.download(list(GRAMMARS))
root = Path(languages.cache_dir()).parent
shutil.rmtree(root / "bundles", ignore_errors=True)
(root / ".download.lock").unlink(missing_ok=True)
PY
"$python" -m PyInstaller \
    --noconfirm --clean --onefile --name ub \
    --distpath "$stage/bin" --workpath "$stage/build" --specpath "$stage" \
    --collect-all crapper --collect-all dryer --collect-all mutator \
    --collect-all tree_sitter --collect-all tree_sitter_language_pack \
    --copy-metadata ub-cli \
    --add-data "$stage/grammar-cache:grammar-cache" \
    --add-data "$stage/bin/LICENSE:." \
    --add-data "$stage/bin/THIRD_PARTY_NOTICES.md:." \
    --add-data "$stage/bin/licenses:licenses" \
    scripts/native_entry.py

"$stage/bin/ub" --version
mkdir -p dist
archive_tmp=$(mktemp dist/.ub-release.XXXXXX)
tar -czf "$archive_tmp" -C "$stage/bin" \
    ub LICENSE THIRD_PARTY_NOTICES.md licenses
mv "$archive_tmp" "dist/ub_${version}_${os}_${arch}.tar.gz"
archive_tmp=
echo "Built dist/ub_${version}_${os}_${arch}.tar.gz"
