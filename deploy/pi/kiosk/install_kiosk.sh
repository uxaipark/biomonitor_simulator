#!/usr/bin/env bash
# Autostart the kiosk with the desktop session of the login user (reTerminal: user "pi" on the 5" display).
set -euo pipefail
PORT="${1:-5445}"
LOGIN_USER="${SUDO_USER:-pi}"; HOME_DIR="$(getent passwd "$LOGIN_USER" | cut -d: -f6)"
apt-get install -y -qq chromium-browser x11-xserver-utils >/dev/null 2>&1 || apt-get install -y -qq chromium x11-xserver-utils
install -m 0755 "$(dirname "$0")/kiosk.sh" /opt/biosim/kiosk.sh
mkdir -p "$HOME_DIR/.config/autostart"
cat > "$HOME_DIR/.config/autostart/biosim-kiosk.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Bio-Signal Emulator kiosk
Exec=/opt/biosim/kiosk.sh $PORT
X-GNOME-Autostart-enabled=true
DESK
chown -R "$LOGIN_USER:$LOGIN_USER" "$HOME_DIR/.config/autostart"
echo "   kiosk autostart installed for $LOGIN_USER (reboot or log in on the display)"
