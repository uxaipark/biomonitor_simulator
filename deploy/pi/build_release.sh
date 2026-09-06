#!/usr/bin/env bash
# Build the Raspberry Pi release tarball on the development machine.
#
#   deploy/pi/build_release.sh                 # code only  -> dist/biosim-pi-<date>.tar.gz  (~1 MB)
#   deploy/pi/build_release.sh --with-bank     # + pre-generated 1-hour loop bank for the Pi config (ecg 250 Hz) (~900 MB)
#   deploy/pi/build_release.sh --version 1.2.0
#
# Excluded on purpose: data/ (runtime state, DB, captures), tests/, ui_mockups/, .venv, caches.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="$(date +%Y%m%d-%H%M)"
WITH_BANK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --with-bank) WITH_BANK=1 ;;
    --version) VERSION="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
NAME="biosim-pi-${VERSION}"
STAGE="$(mktemp -d)/${NAME}"
mkdir -p "$STAGE" "$ROOT/dist"

# 1) application code
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  "$ROOT/emulator" "$ROOT/router" "$ROOT/tools" "$ROOT/scenarios" "$ROOT/run.py" "$ROOT/requirements.txt" "$ROOT/README.md" "$STAGE/"
# 2) deployment files
mkdir -p "$STAGE/deploy"
rsync -a --exclude '__pycache__' "$ROOT/deploy/pi" "$STAGE/deploy/"
cp "$ROOT/deploy/pi/install.sh" "$STAGE/install.sh"
chmod +x "$STAGE/install.sh" "$STAGE/deploy/pi/"*.sh "$STAGE/deploy/pi/kiosk/"*.sh
echo "$VERSION" > "$STAGE/VERSION"

# 3) optional loop bank (must match config.pi.json: ecg_fs / variants_per_rhythm / loop_seconds / seed)
if [ "$WITH_BANK" = 1 ]; then
  FS="$(python3 -c "import json;print(json.load(open('$ROOT/deploy/pi/config.pi.json'))['signals']['ecg_fs'])")"
  SRC="${BIOSIM_DATA_DIR:-$ROOT/data}/loops/$FS"
  if [ ! -d "$SRC" ]; then
    echo "loop bank $SRC not found: run  BIOSIM_DATA_DIR=... .venv/bin/python run.py --pregen  with the Pi settings first" >&2; exit 1
  fi
  mkdir -p "$STAGE/bank/loops"
  rsync -a "$SRC" "$STAGE/bank/loops/"
  echo "included loop bank: $SRC ($(du -sh "$SRC" | cut -f1))"
fi

# 4) tarball
OUT="$ROOT/dist/${NAME}.tar.gz"
COPYFILE_DISABLE=1 tar --no-xattrs --disable-copyfile -C "$(dirname "$STAGE")" -czf "$OUT" "$NAME" 2>/dev/null || COPYFILE_DISABLE=1 tar -C "$(dirname "$STAGE")" -czf "$OUT" "$NAME"   # no macOS xattr headers (GNU tar on the Pi warns about them)
rm -rf "$(dirname "$STAGE")"
echo "built: $OUT ($(du -sh "$OUT" | cut -f1))"
echo "next:  scp $OUT pi@<pi-host>:~/   &&   ssh pi@<pi-host> 'tar xzf ${NAME}.tar.gz && sudo ${NAME}/install.sh'"
