# ComputaMode

Voice-activated workspace launcher + webcam hand gesture control + offline voice commands for Linux/GNOME.

Three systems that work together:

- **[computa-mode/](computa-mode/)** — Voice-activated theme, app launcher, and workspace manager. Say "computa activate" to apply a cyberpunk theme, launch apps, and start hand gesture control.
- **Hand gesture control** — Webcam-based cursor control and system actions using MediaPipe Hands + kernel uinput. Works on Wayland and X11.
- **Voice command layer** — Fully offline push-to-talk voice commands using faster-whisper (Whisper base.en). Triggered by a hand gesture, no internet required.

---

## Gestures

| Gesture | How to make it | Action |
|---|---|---|
| Open palm | All fingers extended | Move cursor; fast sideways flick = switch workspace |
| Pinch | Thumb tip to index tip | Hold left click (drag, highlight, select) |
| Ring pinch | Thumb tip to ring finger tip | Ctrl+scroll zoom in/out (move hand closer/farther) |
| Right click | Thumb tip to middle finger tip | Right click |
| Two-finger | Index + middle extended, others curled | Scroll — move up/down from entry position to set direction + speed |
| Shaka | Thumb + pinky extended, others curled | Alt+Tab; tilt hand left/right to cycle tabs |
| Fist | All fingers curled | Pause/freeze cursor control |
| PTT | Touch thumb tip to pinky tip, hold | Record voice command; release to transcribe + fire |

### Scroll detail
When you enter two-finger scroll, the Y position at that moment becomes your neutral anchor. Hold your hand above it to scroll up, below it to scroll down. Small tilt = slow, large tilt = fast. Return to neutral or drop the gesture to stop.

---

## Voice Commands

Activate: hold thumb tip to pinky tip while speaking, then release.

Chains are supported — say **"and"**, **"then"**, or **"also"** between commands:
> "select all and copy" → Ctrl+A then Ctrl+C
> "close tab then go back" → Ctrl+W then Alt+←

See **[VOICE_COMMANDS.txt](VOICE_COMMANDS.txt)** for the full list. Short version:

- **Tabs**: close tab, new tab, reopen tab, next tab, previous tab
- **Windows**: close window, new window, minimize, maximize, fullscreen
- **Navigation**: go back, go forward, refresh
- **Editing**: copy, paste, cut, undo, redo, select all, save, find
- **Zoom**: zoom in, zoom out, reset zoom
- **Apps**: open browser, open terminal, open files, open editor, open settings
- **Media**: play/pause, volume up/down, mute
- **Scroll**: scroll up, scroll down
- **Gestures**: pause gestures, resume gestures
- **Screenshot**: take screenshot

Fuzzy matching handles transcription errors ("scroll op" → "scroll up"). Customize or add commands by editing `voice_commands.yaml`.

---

## How it works

```
Camera → MediaPipe Hands (21 landmarks) → Gesture classifier → One-Euro filter → uinput virtual mouse
PTT gesture → sounddevice mic → faster-whisper (offline) → fuzzy intent match → action dispatcher
```

The uinput approach creates a kernel-level virtual input device, so it works regardless of display server. pyautogui/pynput use X11's XTEST protocol which Wayland compositors block for security.

---

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

### 3. Install system dependencies

```bash
# xdotool — required for keyboard shortcut voice commands
sudo apt install xdotool

# PortAudio — required for microphone capture (voice layer)
sudo apt install portaudio19-dev
```

### 4. Create venv and install Python dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The first run also downloads the Whisper `base.en` model (~74 MB) into
`~/.cache/huggingface/hub/` automatically. This only happens once.

### 5. Set up uinput permissions

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

### 6. Run calibration (recommended)

Calibration samples your hand + lighting to set gesture detection thresholds
instead of using hardcoded defaults.

```bash
./run.sh --calibrate
```

This saves thresholds to `calibration/thresholds.json` (device-local, gitignored).

### 7. Run

```bash
./run.sh
```

Options:
- `--calibrate` — run calibration before starting
- `--no-overlay` — hide the camera preview window
- `--camera N` — use a different camera index (default: 0)
- `--no-voice` — skip Whisper model load (faster startup, no PTT)

---

## Configuration

Edit `config.yaml` to customize gesture thresholds, smoothing, active region, and actions.

Edit `voice_commands.yaml` to add or modify voice commands. Each intent takes a list of phrases (synonyms Whisper might transcribe) and an action. Actions are either a built-in keyword or any shell command:

```yaml
intents:
  my_command:
    phrases:
      - do the thing
      - do thing
    action: "notify-send 'done'"
```

Built-in action keywords: `scroll_up`, `scroll_down`, `left_click`, `right_click`, `pause_gestures`, `resume_gestures`.

---

## Files

```
├── run.sh                  # Entry point
├── setup_permissions.sh    # udev + group setup (run once per machine)
├── config.yaml             # Gesture mapping + tuning
├── voice_commands.yaml     # Voice intent → action mapping
├── VOICE_COMMANDS.txt      # Human-readable voice command reference
├── requirements.txt        # Python dependencies
├── udev/
│   └── 99-uinput.rules     # udev rule for /dev/uinput access
├── src/
│   ├── main.py             # Main control loop
│   ├── tracker.py          # MediaPipe Hands + OpenCV
│   ├── gestures.py         # Gesture classification from landmarks
│   ├── smoothing.py        # One-Euro filter for cursor smoothing
│   ├── injector.py         # uinput virtual mouse via evdev
│   ├── dispatcher.py       # Shared action executor (gesture + voice)
│   ├── calibration.py      # Interactive calibration mode
│   ├── config.py           # YAML config loader
│   ├── demo_voice.py       # Interactive voice pipeline test
│   ├── demo_mic.py         # Microphone capture test
│   └── voice/
│       ├── transcriber.py  # faster-whisper wrapper
│       ├── intent.py       # Fuzzy intent matcher + chaining
│       └── listener.py     # PTT mic recorder (sounddevice)
├── calibration/            # Device-local thresholds (gitignored)
└── computa-mode/           # Voice-activated workspace launcher
    ├── README.md
    ├── computa-listener.py
    ├── computa-trigger.py
    ├── computa-theme.py
    ├── computa-wallpaper.py
    ├── computa-sounds.py
    ├── speaker_verify.py
    ├── install.sh
    └── uninstall.sh
```

---

## Gitignored (per-machine, never committed)

- `venv/` — Python virtual environment
- `calibration/*.json` — calibration thresholds
- `__pycache__/` — bytecode cache
- `mediapipe/` — MediaPipe model cache
- `faster_whisper_models/` — Whisper model cache (if stored locally)
- `computa-mode/venv/`
- `computa-mode/voice_profile.npy` — speaker enrollment data
