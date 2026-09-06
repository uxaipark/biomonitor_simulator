#!/usr/bin/env bash
# One-shot update from the development machine:  deploy/pi/update.sh pi@192.168.0.50 [dist/biosim-pi-xxx.tar.gz]
# Builds a code-only release (unless a tarball is given), copies it over and runs install.sh --update on the Pi.
set -euo pipefail
HOST="${1:?usage: update.sh user@pi-host [tarball]}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAR="${2:-}"
if [ -z "$TAR" ]; then "$ROOT/deploy/pi/build_release.sh" >/dev/null; TAR="$(ls -t "$ROOT"/dist/biosim-pi-*.tar.gz | head -1)"; fi
NAME="$(basename "$TAR" .tar.gz)"
scp "$TAR" "$HOST:/tmp/"
PORT="${BIOSIM_PORT:-}"                                     # keep the port the Pi already runs on: BIOSIM_PORT=8090 deploy/pi/update.sh user@pi
ssh "$HOST" "cd /tmp && rm -rf $NAME && tar xzf $NAME.tar.gz 2>/dev/null; sudo env ${PORT:+BIOSIM_PORT=$PORT} $NAME/install.sh --update && rm -rf $NAME $NAME.tar.gz"
