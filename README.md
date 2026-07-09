# Hand Gesture Control

Webcam-based hand gesture system for cursor control and system actions on Linux.
Uses MediaPipe Hands for tracking, kernel uinput (via evdev) for input injection.
Works on both **Wayland and X11**.

## How it works

```
Camera → MediaPipe Hands (21 landmarks) → Gesture classifier → One-Euro filter → uinput virtual mouse
```

- **Open palm** — moves cursor (follows palm center)
- **Pinch** (thumb to index tip) — left click
- **Fist** — pause/freeze cursor control
- **Two-finger** (index + middle extended) — vertical scroll

The uinput approach creates a kernel-level virtual input device, so it works
regardless of display server. pyautogui/pynput use X11's XTEST protocol which
Wayland compositors block for security.

## Per-Device Setup

Follow these steps top to bottom on each machine you clone this onto.

### 1. Check your session type

```bash
echo $XDG_SESSION_TYPE
# Should print "wayland" or "x11" — both work.
```

### 2. Check webcam

```bash
ls /dev/video*
# You should see /dev/video0 or similar.
```

### 3. Create venv and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Set up uinput permissions

This gives your user access to `/dev/uinput` without running as root.

```bash
sudo bash setup_permissions.sh
```

This installs a udev rule and adds your user to the `input` group.

**You must log out and log back in** (or reboot) after this step for the
group change to take effect.

Verify it worked (after logging back in):

```bash
source venv/bin/activate
python3 -c "import evdev; d = evdev.UInput(); d.close(); print('uinput OK')"
```

If it prints `uinput OK`, you're ready.

### 5. Run calibration (recommended)

Calibration samples your hand + lighting to set gesture detection thresholds
instead of using hardcoded defaults.

```bash
./run.sh --calibrate
```

This saves thresholds to `calibration/thresholds.json` (device-local, gitignored).

### 6. Run

```bash
./run.sh
```

Options:
- `--calibrate` — run calibration before starting
- `--no-overlay` — hide the camera preview window
- `--camera N` — use a different camera index (default: 0)

## Configuration

Edit `config.yaml` to customize:

- **Gesture thresholds** — pinch distance, finger count for open palm
- **Smoothing** — one-euro filter params (min_cutoff, beta)
- **Active region** — how much of the camera frame maps to the screen
- **Actions** — remap gestures to different actions or shell commands

### Custom shell commands

Any action value that isn't a built-in keyword (`move_cursor`, `left_click`,
`pause`, `scroll`) is executed as a shell command when that gesture is detected.

```yaml
actions:
  fist: "notify-send 'Gesture: Fist'"
  two_finger_scroll: "playerctl play-pause"
```

## Files

```
├── run.sh                  # Entry point
├── setup_permissions.sh    # udev + group setup (run once per machine)
├── config.yaml             # Gesture mapping + tuning (edit this)
├── requirements.txt        # Pinned Python dependencies
├── udev/
│   └── 99-uinput.rules     # udev rule for /dev/uinput access
├── src/
│   ├── main.py             # Main control loop
│   ├── tracker.py          # MediaPipe Hands + OpenCV
│   ├── gestures.py         # Gesture classification from landmarks
│   ├── smoothing.py        # One-Euro filter for cursor smoothing
│   ├── injector.py         # uinput virtual mouse via evdev
│   ├── calibration.py      # Interactive calibration mode
│   └── config.py           # YAML config loader
└── calibration/            # Device-local thresholds (gitignored)
```

## Gitignored (per-machine, never committed)

- `venv/` — Python virtual environment
- `calibration/*.json` — calibration thresholds
- `__pycache__/` — bytecode cache
- `mediapipe/` — downloaded model cache
