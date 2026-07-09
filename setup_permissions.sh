#!/usr/bin/env bash
# Setup uinput permissions so hand-gesture-control can inject input without root.
#
# What this does:
#   1. Installs a udev rule that gives the 'input' group read/write access to /dev/uinput
#   2. Adds your user to the 'input' group
#
# After running this, you MUST log out and log back in (or reboot) for the
# group change to take effect. There is no way around this — group membership
# is set at login time.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UDEV_RULE="$SCRIPT_DIR/udev/99-uinput.rules"
UDEV_DEST="/etc/udev/rules.d/99-uinput.rules"

if [ "$(id -u)" -ne 0 ]; then
    echo "This script needs sudo to install the udev rule and modify groups."
    echo "Usage: sudo bash setup_permissions.sh"
    exit 1
fi

REAL_USER="${SUDO_USER:-$USER}"

echo "=== Hand Gesture Control — Permissions Setup ==="
echo

# 1. Install udev rule
echo "[1/3] Installing udev rule to $UDEV_DEST ..."
cp "$UDEV_RULE" "$UDEV_DEST"
chmod 644 "$UDEV_DEST"

# 2. Reload udev rules
echo "[2/3] Reloading udev rules ..."
udevadm control --reload-rules
udevadm trigger /dev/uinput 2>/dev/null || true

# 3. Add user to input group
echo "[3/3] Adding user '$REAL_USER' to the 'input' group ..."
if id -nG "$REAL_USER" | grep -qw input; then
    echo "  User '$REAL_USER' is already in the 'input' group."
else
    usermod -aG input "$REAL_USER"
    echo "  Added '$REAL_USER' to 'input' group."
fi

echo
echo "=== Done ==="
echo
echo "IMPORTANT: You must LOG OUT and LOG BACK IN (or reboot) for the"
echo "group change to take effect. After that, verify with:"
echo
echo "    python3 -c \"import evdev; d = evdev.UInput(); d.close(); print('uinput OK')\""
echo
echo "If that prints 'uinput OK' without errors, you're ready to go."
