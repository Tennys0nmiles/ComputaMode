"""Offline speech transcription via faster-whisper.

Uses the base.en model for low latency on CPU (~1.5s for a 3s clip).
Model is downloaded once to ~/.cache/huggingface/hub/ and reused.
"""

import threading
import numpy as np

_model = None
_MODEL_SIZE = "base.en"
# Whisper is not thread-safe — only one transcription at a time.
# Subsequent PTT presses queue here and run as soon as the previous finishes.
_transcribe_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        print(f"Loading Whisper model '{_MODEL_SIZE}'...", flush=True)
        _model = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
        print("Whisper model ready.", flush=True)
    return _model


def transcribe(audio_np: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe a numpy int16 audio array to lowercase text.

    Args:
        audio_np: int16 numpy array of audio samples.
        sample_rate: sample rate of the audio (should be 16000).

    Returns:
        Transcribed text, stripped and lowercased. Empty string on failure.
    """
    with _transcribe_lock:
        return _transcribe_locked(audio_np)


def _transcribe_locked(audio_np: np.ndarray) -> str:
    model = _get_model()

    # faster-whisper expects float32 in [-1, 1]
    audio_f32 = audio_np.astype(np.float32) / 32768.0
    if audio_f32.ndim > 1:
        audio_f32 = audio_f32.mean(axis=1)

    # Normalize: only boost genuinely quiet recordings (peak below 35%).
    # Boosting further amplifies noise and degrades Whisper accuracy.
    peak = np.abs(audio_f32).max()
    if 0.0 < peak < 0.35:
        audio_f32 = np.clip(audio_f32 * (0.35 / peak), -1.0, 1.0)

    segments, _ = model.transcribe(
        audio_f32,
        language="en",
        beam_size=1,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={
            "threshold": 0.3,
            "min_silence_duration_ms": 300,
            "min_speech_duration_ms": 100,
        },
    )

    text = " ".join(seg.text for seg in segments).strip().lower()
    return text


def preload():
    """Eagerly load the model so the first PTT isn't slow."""
    _get_model()
