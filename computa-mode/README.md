# Computa-Mode

Voice-activated workspace launcher and cyberpunk theme system for Linux/GNOME.

Press a hotkey, say "computa activate," and it:
1. Applies a cyberpunk theme (dark mode, neon colors, custom fonts, wallpaper)
2. Opens Spotify, GitHub, Claude Code terminal
3. Starts hand gesture control (webcam cursor)
4. Tiles all windows

## How it works

```
Hotkey (Super+`) → computa-trigger.py → Unix socket → computa-listener.py (daemon)
                                                            │
                                                            ├─ Record 5s audio
                                                            ├─ Vosk speech-to-text
                                                            ├─ Speaker verification (optional)
                                                            └─ Launch workspace + gesture control
```

- **computa-listener.py** — Background daemon listening on a Unix socket. Handles voice recognition (Vosk, offline), speaker verification (Resemblyzer), and workspace orchestration.
- **computa-trigger.py** — Lightweight client that sends "ACTIVATE" to the daemon socket. Bound to a hotkey.
- **computa-theme.py** — Applies cyberpunk styling: GNOME dark mode, Share Tech Mono font, neon terminal colors, custom wallpaper.
- **computa-wallpaper.py** — Generates animated cyberpunk wallpaper (WebM) and static fallback (PNG).
- **computa-sounds.py** — Synthesizes activation/login/notification sound effects.
- **speaker_verify.py** — Voice biometric enrollment and verification.

## Per-Device Setup

### 1. Install system dependencies

```bash
sudo apt install python3-venv portaudio19-dev ffmpeg gnome-shell-extensions
```

### 2. Run the installer

```bash
bash install.sh
```

This will:
- Create a Python venv and install pip packages
- Download the Vosk speech model (~40MB)
- Install fonts and cursor theme
- Generate wallpaper, sounds, and visual assets
- Register the hotkey (Super+`)
- Set up autostart for the listener daemon

### 3. Enroll your voice (optional)

For speaker verification so only your voice triggers activation:

```bash
source venv/bin/activate
python3 speaker_verify.py enroll
```

Follow the prompts to record 5 voice samples.

### 4. Log out and log back in

The autostart entry will start the listener daemon on login.

### 5. Use it

Press **Super+`** (backtick), then say **"computa activate"**.

## Voice commands

- **"computa activate"** — Full activation (theme + apps + gesture control)
- **"computa resume"** — Resume previous Claude session

Trigger words: `computa`, `computer`, `computo`

## Integration with hand gesture control

The listener automatically starts `~/hand-gesture-control/run.sh` as part of
the activation sequence. Hand gesture control runs as a separate process and
persists after activation completes. Quit it with `q` in the camera overlay.

## Files (device-local, gitignored)

- `venv/` — Python virtual environment
- `voice_profile.npy` — Speaker enrollment data
- `cyberpunk_wallpaper.bmp` — Generated wallpaper source
