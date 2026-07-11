"""Text-to-speech via Piper (offline, no cloud).

Usage:
    from src.assistant.tts import TTSEngine
    tts = TTSEngine("models/piper/en_US-lessac-high.onnx")
    tts.speak("Hello there.")

Only one utterance plays at a time — concurrent speak() calls queue behind
the current one via a threading lock so the gesture loop is never blocked.
"""

import threading
import numpy as np
import sounddevice as sd
from piper.voice import PiperVoice


class TTSEngine:
    def __init__(self, model_path: str):
        self._voice = PiperVoice.load(model_path)
        self._lock = threading.Lock()

    def speak(self, text: str) -> None:
        """Synthesize text and play through default output device.

        Blocks the calling thread until playback finishes, but since this
        is always called from a background voice-task thread, the main
        gesture loop is unaffected.
        """
        if not text or not text.strip():
            return
        with self._lock:
            try:
                chunks = list(self._voice.synthesize(text.strip()))
                if not chunks:
                    return
                audio = np.concatenate([c.audio_float_array for c in chunks])
                sr = chunks[0].sample_rate
                sd.play(audio, samplerate=sr)
                sd.wait()
            except Exception as exc:
                print(f"[TTS] error: {exc}")
