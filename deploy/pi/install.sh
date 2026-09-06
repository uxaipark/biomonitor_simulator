#!/usr/bin/env bash
# Install / update the Bio-Signal Emulator on a Raspberry Pi 4 (64-bit Raspberry Pi OS).  Run from the unpacked release:
#
#   sudo ./install.sh                # first install: packages, venv, service, sysctl, start
#   sudo ./install.sh --update       # code update only (keeps /var/lib/biosim: config, DB, loop bank)
#   sudo ./install.sh --pregen       # also pre-generate the loop bank now (5-10 min on a Pi 4) instead of at first start
#   sudo ./install.sh --kiosk        # reTerminal: Chromium kiosk autostart on the touch display
#   sudo ./install.sh --no-service   # just files + venv (run by hand: /opt/biosim/venv/bin/python run.py)
#
# Layout:  /opt/biosim/app (code)   /opt/biosim/venv (python)   /var/lib/biosim (data: config.json, DB, loops/, runtime/)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_HOME="${BIOSIM_HOME:-/opt/biosim}"
DATA_DIR="${BIOSIM_DATA:-/var/lib/biosim}"
SVC_USER="${BIOSIM_USER:-biosim}"
PORT="${BIOSIM_PORT:-8080}"
UPDATE=0; PREGEN=0; KIOSK=0; SERVICE=1; DRY=0
for a in "$@"; do
  case "$a" in
    --update) UPDATE=1 ;; --pregen) PREGEN=1 ;; --kiosk) KIOSK=1 ;; --no-service) SERVICE=0 ;; --dry-run) DRY=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

echo "== Bio-Signal Emulator install  (version $(cat "$HERE/VERSION" 2>/dev/null || echo dev))"
echo "   app: $APP_HOME   data: $DATA_DIR   user: $SVC_USER   port: $PORT"

# 1) OS packages (python3-venv is not on the default image; libopenblas is numpy's BLAS on piwheels wheels)
if [ "$UPDATE" = 0 ]; then
  run apt-get update -qq
  run apt-get install -y -qq python3 python3-venv python3-pip libopenblas0-pthread rsync curl
fi

# 2) service user + directories
if ! id "$SVC_USER" >/dev/null 2>&1; then run useradd --system --home "$APP_HOME" --shell /usr/sbin/nologin "$SVC_USER"; fi
run mkdir -p "$APP_HOME/app" "$DATA_DIR"

# 3) code (rsync keeps the venv; --delete removes stale modules)
run rsync -a --delete --exclude 'venv' --exclude '__pycache__' \
  "$HERE/emulator" "$HERE/router" "$HERE/tools" "$HERE/scenarios" "$HERE/run.py" "$HERE/requirements.txt" "$HERE/README.md" "$HERE/deploy" "$HERE/VERSION" "$APP_HOME/app/"

# 4) python venv with piwheels (prebuilt ARM wheels: no compiler, minutes instead of hours)
if [ ! -x "$APP_HOME/venv/bin/python" ]; then run python3 -m venv "$APP_HOME/venv"; fi
run "$APP_HOME/venv/bin/pip" install --quiet --upgrade pip
run "$APP_HOME/venv/bin/pip" install --quiet --extra-index-url https://www.piwheels.org/simple -r "$HERE/deploy/pi/requirements-pi.txt"

# 5) Pi-tuned config on first install only (the GUI edits config.json afterwards)
if [ ! -f "$DATA_DIR/config.json" ]; then run cp "$HERE/deploy/pi/config.pi.json" "$DATA_DIR/config.json"; echo "   wrote $DATA_DIR/config.json (Pi defaults)"; fi
# optional pre-generated loop bank shipped with the release (skips the 5-10 min generation at first start)
if [ -d "$HERE/bank/loops" ]; then run mkdir -p "$DATA_DIR/loops"; run rsync -a "$HERE/bank/loops/" "$DATA_DIR/loops/"; echo "   installed loop bank from release"; fi
run chown -R "$SVC_USER:$SVC_USER" "$APP_HOME" "$DATA_DIR"

# 6) kernel / limits tuning for thousands of gateway sockets
run install -m 0644 "$HERE/deploy/pi/99-biosim.conf" /etc/sysctl.d/99-biosim.conf
run sysctl -q --system || true

# 7) systemd service
if [ "$SERVICE" = 1 ]; then
  if [ "$DRY" = 1 ]; then echo "+ install biosim.service (port $PORT, user $SVC_USER)"; else
    sed -e "s#/opt/biosim#$APP_HOME#g" -e "s#/var/lib/biosim#$DATA_DIR#g" -e "s#User=biosim#User=$SVC_USER#" -e "s#Group=biosim#Group=$SVC_USER#" \
        -e "s#--port 8080#--port $PORT#" "$HERE/deploy/pi/biosim.service" > /etc/systemd/system/biosim.service
    systemctl daemon-reload
    systemctl enable biosim.service >/dev/null
  fi
  if [ "$PREGEN" = 1 ]; then
    echo "   pre-generating the loop bank (Pi 4: 5-10 min)..."
    run sudo -u "$SVC_USER" env BIOSIM_DATA_DIR="$DATA_DIR" "$APP_HOME/venv/bin/python" "$APP_HOME/app/run.py" --pregen
  fi
  run systemctl restart biosim.service
  echo "   service: systemctl status biosim   logs: journalctl -u biosim -f"
fi

# 8) reTerminal kiosk (Chromium full screen on the built-in display, autostart with the desktop session)
if [ "$KIOSK" = 1 ]; then run "$HERE/deploy/pi/kiosk/install_kiosk.sh" "$PORT"; fi

if [ "$SERVICE" = 1 ] && [ "$DRY" = 0 ]; then
  for i in $(seq 1 30); do sleep 1; curl -fs "http://127.0.0.1:$PORT/api/v1/status" >/dev/null 2>&1 && break; done
  "$HERE/deploy/pi/healthcheck.sh" "$PORT" || true
fi
echo "== done.  GUI: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT   API doc: /api/v1"
