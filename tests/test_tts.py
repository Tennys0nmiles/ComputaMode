"""Unit tests for the TTS module.

Run from the repo root:
    python -m pytest tests/test_tts.py -v

These tests do NOT require the Kokoro model files or kokoro-onnx to be
installed — they exercise the pure-Python validation logic and the
error-handling contracts only.
"""

import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest


# ── _validate_blend ───────────────────────────────────────────────────────────

def test_blend_weights_valid():
    from src.assistant.tts import _validate_blend
    # must not raise
    _validate_blend([
        {"voice": "af_nicole", "weight": 0.6},
        {"voice": "af_bella",  "weight": 0.4},
    ])


def test_blend_single_weight_one():
    from src.assistant.tts import _validate_blend
    _validate_blend([{"voice": "af_nicole", "weight": 1.0}])


def test_blend_weights_exceed_one_raises():
    from src.assistant.tts import _validate_blend
    with pytest.raises(ValueError, match="sum to 1.0"):
        _validate_blend([
            {"voice": "af_nicole", "weight": 0.6},
            {"voice": "af_bella",  "weight": 0.5},  # 1.1 total
        ])


def test_blend_weights_below_one_raises():
    from src.assistant.tts import _validate_blend
    with pytest.raises(ValueError, match="sum to 1.0"):
        _validate_blend([
            {"voice": "af_nicole", "weight": 0.4},
            {"voice": "af_bella",  "weight": 0.4},  # 0.8 total
        ])


def test_blend_empty_raises():
    from src.assistant.tts import _validate_blend
    with pytest.raises(ValueError):
        _validate_blend([])


# ── _resolve_voice ────────────────────────────────────────────────────────────

def test_resolve_voice_single_returns_string():
    from src.assistant.tts import _resolve_voice
    result = _resolve_voice(Path("unused.bin"), "af_nicole")
    assert result == "af_nicole"


def test_resolve_voice_blend_returns_array():
    from src.assistant.tts import _resolve_voice
    fake_emb = np.ones((1, 256), dtype=np.float32)
    fake_npz = {"af_nicole": fake_emb, "af_bella": fake_emb * 2}

    with patch("src.assistant.tts.np.load", return_value=fake_npz):
        result = _resolve_voice(
            Path("voices.bin"),
            [{"voice": "af_nicole", "weight": 0.6},
             {"voice": "af_bella",  "weight": 0.4}],
        )

    assert isinstance(result, np.ndarray)
    # weighted average: 0.6*1 + 0.4*2 = 1.4
    assert np.allclose(result, np.full_like(fake_emb, 1.4))


def test_resolve_voice_unknown_name_raises():
    from src.assistant.tts import _resolve_voice
    fake_npz = {"af_nicole": np.zeros((1, 256), dtype=np.float32)}

    with patch("src.assistant.tts.np.load", return_value=fake_npz):
        with pytest.raises(ValueError, match="Unknown Kokoro voice"):
            _resolve_voice(
                Path("voices.bin"),
                [{"voice": "af_nicole",   "weight": 0.5},
                 {"voice": "nonexistent", "weight": 0.5}],
            )


# ── Missing model files ───────────────────────────────────────────────────────

def test_kokoro_missing_model_raises():
    """FileNotFoundError must mention setup.sh so the user knows what to do."""
    from src.assistant.tts import TTSEngine
    config = {
        "engine": "kokoro",
        "model_path": "models/kokoro/does-not-exist.onnx",
        "voices_path": "models/kokoro/does-not-exist.bin",
        "voice": "af_nicole",
    }
    with pytest.raises(FileNotFoundError, match="setup.sh"):
        TTSEngine(config, repo_root=Path("/nonexistent/path"))


def test_kokoro_missing_voices_raises():
    """FileNotFoundError for missing voices file must mention setup.sh."""
    from src.assistant.tts import _KokoroEngine
    with pytest.raises(FileNotFoundError, match="setup.sh"):
        _KokoroEngine(
            model_path=Path("/no/model.onnx"),
            voices_path=Path("/no/voices.bin"),
            voice_spec="af_nicole",
        )


# ── speak() error isolation ───────────────────────────────────────────────────

def test_speak_catches_synthesis_error():
    """speak() must swallow engine exceptions and never propagate to the caller.

    The assistant loop uses  except Exception  around the whole block, but
    speak() itself must be safe to call regardless of what the engine does.
    """
    from src.assistant.tts import TTSEngine

    class _BrokenEngine:
        def synthesize(self, text: str):
            raise RuntimeError("synthesizer exploded")

    engine = TTSEngine.__new__(TTSEngine)
    engine._lock = threading.Lock()
    engine._logger = None
    engine._engine = _BrokenEngine()

    engine.speak("Hello world")  # must not raise
