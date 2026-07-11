"""Stage 2 demo: transcription + intent matching test.

Press Enter to start recording, Enter again to stop.
Prints transcript and matched intent. Tests the full voice pipeline
before wiring it into the gesture system.

Usage:
    python -m src.demo_voice
"""

import os
import sys
from pathlib import Path

from src.voice.listener import PTTRecorder
from src.voice.transcriber import transcribe, preload
from src.voice.intent import IntentMatcher

CONFIG_PATH = Path(__file__).resolve().parent.parent / "voice_commands.yaml"


def main():
    print("Loading Whisper model (downloads ~74MB on first run)...")
    preload()

    matcher = IntentMatcher(str(CONFIG_PATH))
    recorder = PTTRecorder()

    print("\nVoice pipeline ready.")
    print("Press Enter to START recording, Enter again to STOP.")
    print("Type 'q' + Enter to quit.\n")

    while True:
        cmd = input("[ Press Enter to record / q to quit ] ").strip().lower()
        if cmd == "q":
            break

        print("Recording... (press Enter to stop)")
        recorder.start_recording()
        input()
        audio = recorder.stop_recording()

        if len(audio) < 1600:
            print("Too short, nothing captured.\n")
            continue

        duration = len(audio) / 16000
        print(f"Captured {duration:.1f}s of audio. Transcribing...")

        text = transcribe(audio)
        if not text:
            print("(nothing transcribed — too quiet or silence)\n")
            continue

        print(f"Heard: \"{text}\"")

        action, score, phrase = matcher.match(text)
        if action:
            print(f"Matched: \"{phrase}\" → action: {action!r}  (score {score:.2f})")
            print("(action not fired in demo mode)\n")
        else:
            print(f"No match (best score {score:.2f} for \"{phrase}\"). No action taken.\n")


if __name__ == "__main__":
    main()
