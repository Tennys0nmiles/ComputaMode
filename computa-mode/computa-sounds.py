#!/usr/bin/env python3
"""Synthesize cyberpunk sound effects for computa-mode."""

import math
import subprocess
import sys
from pathlib import Path

import numpy as np

COMPUTA_DIR = Path.home() / "computa-mode"
SOUNDS_DIR = COMPUTA_DIR / "assets" / "sounds"
SAMPLE_RATE = 44100


def normalize(audio, peak=0.9):
    """Normalize audio to peak amplitude."""
    mx = np.max(np.abs(audio))
    if mx > 0:
        audio = audio * (peak / mx)
    return audio


def to_int16(audio):
    """Convert float audio to int16."""
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16)


def save_wav(audio, path):
    """Save audio as WAV file."""
    import wave
    data = to_int16(audio)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(data.tobytes())


def wav_to_ogg(wav_path, ogg_path):
    """Convert WAV to OGG using ffmpeg."""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(wav_path),
            "-c:a", "libvorbis", "-q:a", "5",
            str(ogg_path),
        ], check=True, capture_output=True)
        wav_path.unlink()
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"  Warning: ffmpeg failed, keeping WAV: {wav_path}")
        return False


def generate_activate_sound():
    """Generate ascending synth sweep with digital artifacts (0.5s)."""
    duration = 0.5
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    # Ascending frequency sweep 200Hz -> 2000Hz
    freq = 200 + 1800 * (t / duration) ** 2
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)

    # Main tone (saw-like via harmonics)
    audio = np.sin(phase) * 0.5
    audio += np.sin(phase * 2) * 0.2
    audio += np.sin(phase * 3) * 0.1

    # Digital click at start
    click_env = np.exp(-t * 80)
    click = np.sin(2 * np.pi * 4000 * t) * click_env * 0.3
    audio += click

    # Amplitude envelope (fade in, sustain, quick fade out)
    env = np.minimum(t * 20, 1.0) * np.exp(-(t - duration) ** 2 / 0.02)
    env = np.minimum(env, 1.0)
    audio *= env

    # Add some shimmer (high frequency modulation)
    shimmer = np.sin(2 * np.pi * 8000 * t) * np.sin(2 * np.pi * 3 * t) * 0.05
    audio += shimmer

    return normalize(audio)


def generate_login_sound():
    """Generate ambient digital drone with artifacts (2s)."""
    duration = 2.0
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    # Deep base drone
    audio = np.sin(2 * np.pi * 80 * t) * 0.3
    audio += np.sin(2 * np.pi * 120 * t) * 0.2
    audio += np.sin(2 * np.pi * 160 * t) * 0.1

    # Evolving pad (slow frequency modulation)
    mod = np.sin(2 * np.pi * 0.5 * t) * 50
    audio += np.sin(2 * np.pi * (300 + mod) * t) * 0.15
    audio += np.sin(2 * np.pi * (450 + mod * 1.5) * t) * 0.08

    # Digital artifacts — short bursts of noise
    for burst_time in [0.3, 0.7, 1.1, 1.5]:
        burst_env = np.exp(-((t - burst_time) ** 2) / 0.001)
        noise = (np.random.random(len(t)) * 2 - 1) * burst_env * 0.15
        audio += noise

    # Rising pitch element
    rise_freq = 200 + 600 * (t / duration)
    rise_phase = np.cumsum(2 * np.pi * rise_freq / SAMPLE_RATE)
    rise = np.sin(rise_phase) * 0.08 * (t / duration)
    audio += rise

    # Resonant ping at 0.5s
    ping_env = np.exp(-np.maximum(t - 0.5, 0) * 8)
    ping = np.sin(2 * np.pi * 1200 * t) * ping_env * 0.2
    audio += ping

    # Overall envelope
    env = np.minimum(t * 3, 1.0)  # fade in
    env *= np.minimum((duration - t) * 2, 1.0)  # fade out
    audio *= env

    return normalize(audio)


def generate_notification_sound():
    """Generate brief digital chime (0.3s)."""
    duration = 0.3
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    # Two-tone chime (ascending)
    tone1_env = np.exp(-t * 15)
    tone1 = np.sin(2 * np.pi * 1200 * t) * tone1_env * 0.5

    tone2_start = 0.08
    tone2_t = np.maximum(t - tone2_start, 0)
    tone2_env = np.exp(-tone2_t * 12)
    tone2 = np.sin(2 * np.pi * 1800 * t) * tone2_env * 0.4
    tone2 *= (t >= tone2_start).astype(float)

    audio = tone1 + tone2

    # Add shimmer
    shimmer = np.sin(2 * np.pi * 6000 * t) * np.exp(-t * 20) * 0.1
    audio += shimmer

    # Quick fade out
    env = np.minimum((duration - t) * 15, 1.0)
    audio *= env

    return normalize(audio)


def main():
    print("=== COMPUTA SOUND GENERATOR ===")
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)

    sounds = {
        "activate": generate_activate_sound,
        "login": generate_login_sound,
        "notification": generate_notification_sound,
    }

    for name, gen_func in sounds.items():
        print(f"Generating {name}...")
        audio = gen_func()
        wav_path = SOUNDS_DIR / f"{name}.wav"
        ogg_path = SOUNDS_DIR / f"{name}.ogg"
        save_wav(audio, wav_path)
        if wav_to_ogg(wav_path, ogg_path):
            print(f"  Saved: {ogg_path}")
        else:
            print(f"  Saved: {wav_path}")

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
