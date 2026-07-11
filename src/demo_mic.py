"""Stage 1 mic test: capture 3 seconds of audio and save to /tmp/mic_test.wav.

Run this to confirm your mic is detected and producing audio before building
the voice layer.

Usage:
    python -m src.demo_mic
"""

import wave
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
DURATION = 3
CHANNELS = 1
OUTPUT = "/tmp/mic_test.wav"


def main():
    print(f"Input device: {sd.query_devices(kind='input')['name']}")
    print(f"Recording {DURATION}s at {SAMPLE_RATE}Hz... speak now!")

    audio = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=CHANNELS, dtype="int16")
    sd.wait()

    peak = np.abs(audio).max()
    rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
    print(f"Peak: {peak}  RMS: {rms:.1f}")

    if peak < 100:
        print("WARNING: Very low signal — mic may be muted or wrong device.")
    else:
        print("Signal looks good.")

    with wave.open(OUTPUT, "w") as f:
        f.setnchannels(CHANNELS)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(audio.tobytes())

    print(f"Saved to {OUTPUT}")
    print("Play back with:  aplay /tmp/mic_test.wav")


if __name__ == "__main__":
    main()
