#!/usr/bin/env bash
# Chromium kiosk on the reTerminal display: waits for the emulator, then opens the GUI full screen (touch friendly).
PORT="${1:-5445}"; URL="http://127.0.0.1:$PORT/"
for i in $(seq 1 60); do curl -fs "$URL/api/v1/status" >/dev/null 2>&1 && break; sleep 2; done
xset s off; xset -dpms; xset s noblank 2>/dev/null || true
BROWSER="$(command -v chromium-browser || command -v chromium)"
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 \
  --overscroll-history-navigation=0 --touch-events=enabled --window-size=1280,720 --window-position=0,0 "$URL"
