#!/usr/bin/env python3
"""Send activation trigger to computa-listener via Unix socket.

This script is called by the Super+V GNOME keybinding.
It connects to the listener's socket and sends "ACTIVATE".
"""

import os
import socket
import sys

SOCKET_PATH = f"/run/user/{os.getuid()}/computa-listener.sock"

try:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)
    sock.sendall(b"ACTIVATE")
    sock.close()
except ConnectionRefusedError:
    # Listener not running — show a notification
    import subprocess
    subprocess.run([
        "notify-send", "--urgency=normal",
        "--icon=audio-input-microphone",
        "Computa Mode",
        "Listener not running. Start it with:\npython3 ~/computa-mode/computa-listener.py",
    ], check=False)
except FileNotFoundError:
    import subprocess
    subprocess.run([
        "notify-send", "--urgency=normal",
        "--icon=dialog-warning",
        "Computa Mode",
        "Listener socket not found. Is computa-listener.py running?",
    ], check=False)
