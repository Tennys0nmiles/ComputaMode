#!/usr/bin/env python3
"""Computa-mode voice listener daemon for Linux.

Listens on a Unix domain socket for trigger signals (from Ctrl+` hotkey).
On trigger: records audio, runs Vosk STT, verifies speaker, launches Claude Code.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly
from math import gcd

COMPUTA_DIR = Path(__file__).resolve().parent
VENV_PYTHON = COMPUTA_DIR / "venv" / "bin" / "python3"
VOSK_MODEL_DIR = Path.home() / ".local" / "share" / "computa-mode" / "vosk-model-small-en-us-0.15"
SOCKET_PATH = Path(f"/run/user/{os.getuid()}/computa-listener.sock")
SOUNDS_DIR = COMPUTA_DIR / "assets" / "sounds"

VOSK_RATE = 16000  # Rate Vosk expects
RECORD_DURATION = 5  # seconds
CONFIDENCE_MIN = 0.65
VERIFY_THRESHOLD = 0.65

# Concurrency lock — only one trigger at a time
_trigger_lock = threading.Lock()

# Will be loaded lazily
_vosk_model = None
_vosk_recognizer = None


def log(msg, color="\033[0m"):
    """Print a timestamped log message."""
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{color}[{ts}] {msg}\033[0m", flush=True)


def log_cyan(msg):
    log(msg, "\033[36m")


def log_green(msg):
    log(msg, "\033[32m")


def log_magenta(msg):
    log(msg, "\033[35m")


def log_red(msg):
    log(msg, "\033[31m")


def log_yellow(msg):
    log(msg, "\033[33m")


def play_sound(name):
    """Play a sound file using paplay (PipeWire/PulseAudio)."""
    for ext in [".ogg", ".wav"]:
        path = SOUNDS_DIR / f"{name}{ext}"
        if path.exists():
            # Try available audio players in order
            for player in ["pw-play", "paplay", "aplay", "ffplay"]:
                try:
                    cmd = [player, str(path)]
                    if player == "ffplay":
                        cmd = ["ffplay", "-nodisp", "-autoexit", str(path)]
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except FileNotFoundError:
                    continue
            log_yellow(f"No audio player found for: {name}")
            return
    log_yellow(f"Sound not found: {name}")


def load_vosk():
    """Lazily load the Vosk model and recognizer."""
    global _vosk_model, _vosk_recognizer
    if _vosk_model is None:
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)  # Suppress Vosk logs

        model_path = str(VOSK_MODEL_DIR)
        if not VOSK_MODEL_DIR.exists():
            log_red(f"Vosk model not found at {model_path}")
            log_red("Run install.sh to download it")
            sys.exit(1)

        log_cyan("Loading Vosk model...")
        _vosk_model = Model(model_path)
        _vosk_recognizer = KaldiRecognizer(_vosk_model, VOSK_RATE)
        _vosk_recognizer.SetWords(True)
        log_green("Vosk model loaded")

    return _vosk_recognizer


def ensure_mic_unmuted():
    """Ensure the default microphone source is unmuted and at a reasonable volume."""
    try:
        # Unmute the default source
        subprocess.run(
            ["wpctl", "set-mute", "@DEFAULT_SOURCE@", "0"],
            capture_output=True, timeout=5
        )
        # Check current volume
        result = subprocess.run(
            ["wpctl", "get-volume", "@DEFAULT_SOURCE@"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Output is like "Volume: 0.40" or "Volume: 0.40 [MUTED]"
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                vol = float(parts[1])
                if vol < 0.2:
                    log_yellow(f"Mic volume very low ({int(vol * 100)}%), raising to 40%")
                    subprocess.run(
                        ["wpctl", "set-volume", "@DEFAULT_SOURCE@", "0.4"],
                        capture_output=True, timeout=5
                    )
        log_cyan("Mic unmuted and ready")
    except Exception as e:
        log_yellow(f"Could not unmute mic: {e}")


def record_audio():
    """Record audio for RECORD_DURATION seconds at native rate, resample to VOSK_RATE."""
    # Query the device's native sample rate to avoid PipeWire ALSA hangs
    dev_info = sd.query_devices(sd.default.device[0], 'input')
    native_rate = int(dev_info['default_samplerate'])
    log_cyan(f"Recording {RECORD_DURATION}s of audio (device rate: {native_rate} Hz)...")

    audio = sd.rec(
        int(RECORD_DURATION * native_rate),
        samplerate=native_rate,
        channels=1,
        dtype="float32",
        blocking=True,
    )
    audio = audio.flatten()

    # Resample to VOSK_RATE if needed
    if native_rate != VOSK_RATE:
        g = gcd(native_rate, VOSK_RATE)
        audio = resample_poly(audio, VOSK_RATE // g, native_rate // g)

    # Convert to int16
    audio = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    return audio


def save_wav(audio, path):
    """Save int16 audio to a WAV file."""
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(VOSK_RATE)
        wf.writeframes(audio.tobytes())


def recognize_speech(audio):
    """Run Vosk STT on audio buffer. Returns (text, confidence)."""
    recognizer = load_vosk()

    # Reset recognizer state
    recognizer.Reset()

    # Feed audio in chunks
    chunk_size = 4000
    audio_bytes = audio.tobytes()
    for i in range(0, len(audio_bytes), chunk_size):
        recognizer.AcceptWaveform(audio_bytes[i:i + chunk_size])

    result = json.loads(recognizer.FinalResult())
    text = result.get("text", "").lower().strip()

    # Calculate rough confidence from word-level results
    confidence = 0.0
    if "result" in result and result["result"]:
        confs = [w.get("conf", 0) for w in result["result"]]
        confidence = sum(confs) / len(confs) if confs else 0.0

    return text, confidence


def verify_speaker(wav_path):
    """Run speaker verification against enrolled profile."""
    voice_profile = COMPUTA_DIR / "voice_profile.npy"
    if not voice_profile.exists():
        log_yellow("No voice profile enrolled -- skipping speaker verification")
        return True

    try:
        python = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"
        result = subprocess.run(
            [python, str(COMPUTA_DIR / "speaker_verify.py"),
             "verify", "--audio", str(wav_path), "--threshold", str(VERIFY_THRESHOLD)],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        score = round(data["score"] * 100)

        if data["verified"]:
            log_cyan(f"Speaker VERIFIED (score: {score}%)")
            return True
        else:
            log_red(f"Speaker REJECTED (score: {score}%)")
            return False
    except Exception as e:
        log_red(f"Speaker verification error: {e}")
        return False


def apply_theme():
    """Apply cyberpunk theme."""
    log_magenta("Applying cyberpunk theme...")
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"
    subprocess.run(
        [python, str(COMPUTA_DIR / "computa-theme.py"), "apply"],
        capture_output=True, timeout=30
    )


def speak(name):
    """Play a pre-generated voice line using Piper TTS."""
    wav_file = SOUNDS_DIR / f"{name}.wav"
    if wav_file.exists():
        subprocess.Popen(
            ["pw-play", str(wav_file)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        log_yellow(f"Voice line not found: {wav_file}")


def tile_windows():
    """Tile windows using computa-tiler GNOME Shell extension via DBus.

    Layout: Spotify right half, browser bottom-left, terminal top-left.
    Requires computa-tiler@computa-mode extension to be enabled (needs re-login after first install).
    """
    layout = "spotify:right-half,firefox:bottom-left,chromium:bottom-left,chrome:bottom-left,gnome-terminal:top-left"
    try:
        result = subprocess.run([
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Shell",
            "--object-path", "/com/computa/Tiler",
            "--method", "com.computa.Tiler.TileAll",
            layout,
        ], capture_output=True, text=True, timeout=5)
        if "true" in result.stdout:
            log_green("Windows tiled successfully")
        else:
            log_yellow("Window tiling not available (re-login required to load extension)")
    except Exception as e:
        log_yellow(f"Could not tile windows: {e}")


def kill_existing_claude():
    """Kill any existing Claude CLI processes and clean up lock files."""
    import shutil
    try:
        # Kill claude process and all child processes
        result = subprocess.run(
            ["pkill", "-x", "claude"],
            capture_output=True, timeout=5
        )
        if result.returncode == 0:
            log_yellow("Killed existing Claude session")
            # Wait for process to actually die
            subprocess.run(["pkill", "-x", "-KILL", "claude"],
                           capture_output=True, timeout=5)
        # Clean up Claude's temp/lock directory so new session starts immediately
        claude_tmp = Path(f"/tmp/claude-{os.getuid()}")
        if claude_tmp.exists():
            shutil.rmtree(claude_tmp, ignore_errors=True)
            log_yellow("Cleaned up Claude lock files")
        # Also clean task lock files
        claude_tasks = Path.home() / ".claude" / "tasks"
        if claude_tasks.exists():
            for lock_file in claude_tasks.glob("*/.lock"):
                lock_file.unlink(missing_ok=True)
            log_yellow("Cleaned up Claude task locks")
        time.sleep(2)
    except Exception as e:
        log_yellow(f"Could not kill existing Claude: {e}")


def launch_claude(resume=False):
    """Launch Claude Code in a new terminal."""
    kill_existing_claude()

    claude_cmd = "claude --dangerously-skip-permissions"
    if resume:
        claude_cmd += " -r"
        log_green("Launching Claude Code (resume)...")
    else:
        log_green("Launching Claude Code...")

    bash_cmd = f"unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT; {claude_cmd}; exec bash"
    # Clean environment for the subprocess
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    # Try gnome-terminal first, then common alternatives
    terminals = [
        ["gnome-terminal", "--", "bash", "-c", bash_cmd],
        ["xterm", "-e", "bash", "-c", bash_cmd],
        ["x-terminal-emulator", "-e", "bash", "-c", bash_cmd],
    ]

    for cmd in terminals:
        try:
            subprocess.Popen(cmd, start_new_session=True, env=clean_env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue

    log_red("No terminal emulator found! Install gnome-terminal.")


def handle_trigger():
    """Handle a voice activation trigger (called when hotkey is pressed)."""
    if not _trigger_lock.acquire(blocking=False):
        log_yellow("Trigger already in progress, ignoring")
        return
    try:
        _handle_trigger_inner()
    finally:
        _trigger_lock.release()


def _handle_trigger_inner():
    """Inner trigger handler (runs under lock)."""
    play_sound("notification")  # "listening" beep

    # Ensure mic is unmuted before recording
    ensure_mic_unmuted()

    # Record audio
    audio = record_audio()

    # Speech recognition
    text, confidence = recognize_speech(audio)
    log_cyan(f"Heard: '{text}' (confidence: {confidence:.0%})")
    subprocess.Popen([
        "notify-send", "--urgency=normal", "--icon=audio-input-microphone",
        "Computa Mode", f"Heard: '{text}' ({confidence:.0%} confidence)",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Check for activation phrase
    trigger_words = ["computa", "computer", "computo"]
    has_trigger = any(w in text for w in trigger_words)
    has_activate = "activate" in text
    has_resume = "resume" in text

    if has_trigger and (has_activate or has_resume):
        mode = "resume" if has_resume else "activate"
        log_green(f"Activation phrase detected! (mode: {mode})")

        # Save audio for speaker verification
        wav_path = Path(tempfile.mktemp(suffix=".wav", prefix="computa_verify_"))
        save_wav(audio, wav_path)

        try:
            if verify_speaker(wav_path):
                play_sound("activate")
                speak("welcome")
                apply_theme()

                # Launch all apps then tile
                def launch_and_tile():
                    # Spotify
                    subprocess.Popen(["xdg-open", "spotify:search:breakcore"],
                                     start_new_session=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(3)
                    # Auto-play via Spotify dbus
                    try:
                        subprocess.run([
                            "dbus-send", "--print-reply",
                            "--dest=org.mpris.MediaPlayer2.spotify",
                            "/org/mpris/MediaPlayer2",
                            "org.mpris.MediaPlayer2.Player.Play",
                        ], capture_output=True, timeout=5)
                        log_green("Spotify playback started")
                    except Exception:
                        log_yellow("Could not auto-play Spotify")

                    # GitHub
                    subprocess.Popen(["xdg-open", "https://github.com/login"],
                                     start_new_session=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(2)

                    # Claude terminal (launched last so it has focus)
                    launch_claude(resume=(mode == "resume"))
                    time.sleep(3)

                    # Hand gesture control
                    gesture_dir = COMPUTA_DIR.parent
                    if gesture_dir.exists():
                        gesture_run = gesture_dir / "run.sh"
                        if gesture_run.exists():
                            subprocess.Popen(
                                ["bash", str(gesture_run)],
                                start_new_session=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                cwd=str(gesture_dir),
                            )
                            log_green("Hand gesture control started")
                        else:
                            log_yellow("Hand gesture control: run.sh not found")
                    else:
                        log_yellow("Hand gesture control not installed")

                    # Tile everything
                    tile_windows()
                    log_green("Workspace launch complete")

                threading.Thread(target=launch_and_tile, daemon=True).start()
            else:
                log_red("Access denied -- speaker not verified")
        finally:
            wav_path.unlink(missing_ok=True)
    else:
        log_yellow("Activation phrase not recognized. Say 'computa activate'.")


def cleanup_socket():
    """Remove stale socket file."""
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()


def run_server():
    """Run the Unix domain socket server."""
    cleanup_socket()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    server.listen(1)

    # Clean up on exit
    def on_exit(sig, frame):
        cleanup_socket()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    # Pre-load the Vosk model
    load_vosk()

    # Startup banner
    voice_profile_exists = (COMPUTA_DIR / "voice_profile.npy").exists()

    print()
    print("\033[36m========================================\033[0m")
    print("\033[32m  COMPUTA LISTENER ACTIVE\033[0m")
    print("\033[32m  Press Ctrl+` then say\033[0m")
    print("\033[32m  'computa activate' to launch\033[0m")
    print(f"\033[36m  Confidence threshold: {int(CONFIDENCE_MIN * 100)}%\033[0m")
    if voice_profile_exists:
        print("\033[32m  Speaker verification: ON\033[0m")
    else:
        print("\033[33m  Speaker verification: OFF (no profile)\033[0m")
        print("\033[33m  Enroll: python3 speaker_verify.py enroll\033[0m")
    print("\033[33m  Press Ctrl+C to stop\033[0m")
    print("\033[36m========================================\033[0m")
    print()

    while True:
        try:
            conn, _ = server.accept()
            data = conn.recv(1024).decode("utf-8").strip()
            conn.close()

            if data == "ACTIVATE":
                # Handle in a thread so socket stays responsive
                t = threading.Thread(target=handle_trigger, daemon=True)
                t.start()
        except Exception as e:
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                break
            log_red(f"Server error: {e}")


def main():
    run_server()


if __name__ == "__main__":
    main()
