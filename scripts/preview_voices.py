#!/usr/bin/env python3
"""Generate test WAV files for Kokoro female voice selection.

Run from the repo root (with venv active):

    python3 scripts/preview_voices.py

Fifteen WAV files are written to  voice_previews/  — one per candidate female
voice.  Listen to them and pick your favourite, then set:

    assistant.tts.voice: <name>

in voice_commands.yaml.

No arguments needed.  Requires setup.sh to have been run first.
"""

from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

REPO = Path(__file__).resolve().parent.parent
MODEL_PATH  = REPO / "models" / "kokoro" / "kokoro-v1.0.onnx"
VOICES_PATH = REPO / "models" / "kokoro" / "voices-v1.0.bin"
OUT_DIR     = REPO / "voice_previews"

SENTENCE = (
    "Hello, I'm Nova. Battery is at eighty percent, "
    "CPU usage is low. How can I help you today?"
)

# All American female (af_*) and British female (bf_*) voices in Kokoro v1.0
FEMALE_VOICES = [
    # American female
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    # British female
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
]


def main() -> None:
    if not MODEL_PATH.exists() or not VOICES_PATH.exists():
        raise SystemExit(
            "Kokoro model files not found.  Run setup.sh first.\n"
            f"  Expected: {MODEL_PATH}\n"
            f"            {VOICES_PATH}"
        )

    OUT_DIR.mkdir(exist_ok=True)
    kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))

    print(f"Rendering {len(FEMALE_VOICES)} voices → {OUT_DIR}/\n")
    for voice in FEMALE_VOICES:
        out = OUT_DIR / f"test_{voice}.wav"
        print(f"  {voice:<16} → {out.name} ...", end=" ", flush=True)
        try:
            samples, sr = kokoro.create(SENTENCE, voice=voice, speed=1.0, lang="en-us")
            sf.write(str(out), samples, sr)
            print("ok")
        except Exception as exc:
            print(f"FAILED: {exc}")

    print(f"\nDone.  Listen to files in {OUT_DIR}/")
    print("Then set  assistant.tts.voice  in voice_commands.yaml.")


if __name__ == "__main__":
    main()
