#!/usr/bin/env bash
# Entry point for hand-gesture-control.
# Usage:
#   ./run.sh              — run with defaults
#   ./run.sh --calibrate  — run calibration first
#   ./run.sh --no-overlay — hide camera overlay window
#   ./run.sh --camera 1   — use a different camera index

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv
if [ ! -d "venv" ]; then
    echo "No venv found. Creating one and installing dependencies..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# Quick uinput access check
python3 -c "
import evdev
try:
    d = evdev.UInput(); d.close()
except PermissionError:
    print()
    print('ERROR: Cannot access /dev/uinput.')
    print('Run:  sudo bash setup_permissions.sh')
    print('Then: log out and log back in.')
    exit(1)
"

exec python -m src.main "$@"
