"""Text-to-speech — Kokoro-ONNX (default) or Piper (legacy fallback).

Voice config lives in voice_commands.yaml under  assistant.tts:

    tts:
      engine: kokoro                          # or: piper (legacy)
      model_path: models/kokoro/kokoro-v1.0.onnx
      voices_path: models/kokoro/voices-v1.0.bin
      voice: af_nicole                        # single voice name
      speed: 1.0
      # To blend voices, comment out 'voice' and uncomment 'blend':
      # blend:
      #   - voice: af_nicole
      #     weight: 0.6
      #   - voice: af_bella
      #     weight: 0.4

The public interface is TTSEngine.speak(text).

For multi-sentence responses, synthesis and playback are pipelined:
sentence N+1 is synthesized while sentence N plays, so there is no
long silent wait before output starts.

Synthesis latency is logged per utterance to the JSONL log (tier="tts").
"""

import queue
import re
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd


# Sentence boundary: . ! ? ; followed by whitespace
_SENT_RE = re.compile(r'(?<=[.!?;])\s+')


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for streaming synthesis."""
    parts = [s.strip() for s in _SENT_RE.split(text)]
    return [p for p in parts if p]


def _validate_blend(blend: list) -> None:
    """Raise ValueError if blend weights don't sum to 1.0 (±0.001 tolerance)."""
    if not blend:
        raise ValueError("blend list must not be empty.")
    total = sum(float(e["weight"]) for e in blend)
    if abs(total - 1.0) > 1e-3:
        raise ValueError(
            f"Blend weights must sum to 1.0 (got {total:.4f}). "
            "Check  assistant.tts.blend  in voice_commands.yaml."
        )


def _resolve_voice(voices_path: Path, voice_spec) -> "np.ndarray | str":
    """Return a voice name (str) for a single voice, or a blended embedding
    (np.ndarray) for a blend spec.  The np.ndarray path is passed directly
    to kokoro.create() as the voice parameter."""
    if isinstance(voice_spec, str):
        return voice_spec

    # Blend: weighted average of voice embedding tensors
    _validate_blend(voice_spec)
    voices_npz = np.load(str(voices_path), allow_pickle=False)
    embedding: Optional[np.ndarray] = None
    for entry in voice_spec:
        name = entry["voice"]
        weight = float(entry["weight"])
        if name not in voices_npz:
            available = sorted(voices_npz)
            raise ValueError(
                f"Unknown Kokoro voice '{name}'. "
                f"Available voices: {available}"
            )
        v = voices_npz[name].astype(np.float32)
        embedding = v * weight if embedding is None else embedding + v * weight
    return embedding  # type: ignore[return-value]


# ── Engine implementations ────────────────────────────────────────────────────

class _KokoroEngine:
    """Kokoro-ONNX synthesis at 24 kHz, CPU-only."""

    def __init__(self, model_path: Path, voices_path: Path,
                 voice_spec, speed: float = 1.0):
        # Check files before importing so that the missing-file error is clear
        # even when kokoro-onnx is not installed in the current env.
        if not model_path.exists():
            raise FileNotFoundError(
                f"Kokoro model not found: {model_path}\n"
                "Run setup.sh to download it."
            )
        if not voices_path.exists():
            raise FileNotFoundError(
                f"Kokoro voices file not found: {voices_path}\n"
                "Run setup.sh to download it."
            )

        try:
            from kokoro_onnx import Kokoro  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "kokoro-onnx is not installed. Run setup.sh to install it."
            ) from exc

        self._kokoro = Kokoro(str(model_path), str(voices_path))
        self._speed = speed
        self._voice = _resolve_voice(voices_path, voice_spec)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        samples, sr = self._kokoro.create(
            text, voice=self._voice, speed=self._speed, lang="en-us"
        )
        return samples.astype(np.float32), int(sr)


class _PiperEngine:
    """Legacy Piper TTS at 22 kHz.  Keep until Kokoro is confirmed in daily use,
    then remove this class and the piper-tts dependency together."""

    def __init__(self, model_path: Path):
        if not model_path.exists():
            raise FileNotFoundError(
                f"Piper model not found: {model_path}\n"
                "Run setup.sh to download it."
            )
        try:
            from piper.voice import PiperVoice  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "piper-tts is not installed. Run setup.sh or switch "
                "assistant.tts.engine to  kokoro  in voice_commands.yaml."
            ) from exc
        self._voice = PiperVoice.load(str(model_path))

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return np.array([], dtype=np.float32), 22050
        audio = np.concatenate([c.audio_float_array for c in chunks])
        return audio.astype(np.float32), int(chunks[0].sample_rate)


# ── Public interface ──────────────────────────────────────────────────────────

class TTSEngine:
    """Synthesize text and play through the default output device.

    Thread-safe: concurrent speak() calls queue behind a lock so the
    gesture loop is never blocked.
    """

    def __init__(self, config: dict, repo_root: Optional[Path] = None,
                 logger=None):
        """
        Args:
            config:    dict from voice_commands.yaml  assistant.tts
            repo_root: project root for resolving relative model paths
            logger:    optional CommandLogger; logs synthesis latency
        """
        self._lock = threading.Lock()
        self._logger = logger
        root = repo_root or Path(__file__).resolve().parent.parent.parent

        engine = config.get("engine", "kokoro")

        if engine == "kokoro":
            model_path = root / config.get("model_path",
                                           "models/kokoro/kokoro-v1.0.onnx")
            voices_path = root / config.get("voices_path",
                                            "models/kokoro/voices-v1.0.bin")
            speed = float(config.get("speed", 1.0))
            blend = config.get("blend")
            voice_spec = blend if blend else config.get("voice", "af_nicole")
            self._engine: _KokoroEngine | _PiperEngine = _KokoroEngine(
                model_path, voices_path, voice_spec, speed
            )

        elif engine == "piper":
            model_path = root / config.get("model_path",
                                           "models/piper/en_US-lessac-high.onnx")
            self._engine = _PiperEngine(model_path)

        else:
            raise ValueError(
                f"Unknown TTS engine '{engine}'. "
                "Set  assistant.tts.engine  to  kokoro  or  piper  "
                "in voice_commands.yaml."
            )

    def speak(self, text: str) -> None:
        """Synthesize and play text.  Blocks calling thread until done.

        Called from a background voice-task thread, so the main gesture
        loop is never blocked.  Multiple calls serialize through the lock.
        """
        if not text or not text.strip():
            return
        with self._lock:
            sentences = _split_sentences(text.strip())
            if len(sentences) <= 1:
                self._speak_single(text.strip())
            else:
                self._speak_streaming(sentences)

    # ── private helpers ───────────────────────────────────────────────────────

    def _speak_single(self, text: str) -> None:
        t0 = time.monotonic()
        try:
            audio, sr = self._engine.synthesize(text)
        except Exception as exc:
            print(f"[TTS] error: {exc}")
            return
        synth_ms = int((time.monotonic() - t0) * 1000)
        print(f"[TTS] synth={synth_ms}ms", flush=True)
        self._log(text, synth_ms)
        if audio.size:
            sd.play(audio, samplerate=sr)
            sd.wait()

    def speak_iter(self, sentences) -> None:
        """Synthesize and play a lazy iterable of sentences.

        Use this for streaming LLM responses — sentences are yielded by the
        brain as tokens arrive, so synthesis of sentence N starts as soon as
        the LLM finishes generating it, while sentence N-1 is still playing.
        Blocks calling thread until all sentences have played.
        """
        with self._lock:
            self._speak_streaming(sentences)

    def _speak_streaming(self, sentences) -> None:
        """Pipeline: sentence N+1 synthesizes while sentence N plays.
        Accepts any iterable — list or lazy generator."""
        q: queue.Queue = queue.Queue(maxsize=2)

        def _synth_worker() -> None:
            total_ms = 0
            all_text: list[str] = []
            for s in sentences:
                all_text.append(s)
                t0 = time.monotonic()
                try:
                    audio, sr = self._engine.synthesize(s)
                    ms = int((time.monotonic() - t0) * 1000)
                    total_ms += ms
                    q.put((audio, sr, ms))
                except Exception as exc:
                    print(f"[TTS] error: {exc}")
                    q.put(None)
                    return
            q.put(None)  # playback sentinel
            print(
                f"[TTS] total synth={total_ms}ms ({len(all_text)} sentences)",
                flush=True,
            )
            self._log(" ".join(all_text), total_ms)

        synth_thread = threading.Thread(target=_synth_worker, daemon=True)
        synth_thread.start()

        while True:
            item = q.get()
            if item is None:
                break
            audio, sr, ms = item
            print(f"[TTS] chunk synth={ms}ms", flush=True)
            if audio.size:
                sd.play(audio, samplerate=sr)
                sd.wait()

        synth_thread.join()

    def _log(self, text: str, synth_ms: int) -> None:
        if self._logger is not None:
            # tier="tts", intent=None, confidence=0.0; latency_ms = synth time
            self._logger.log(text, "tts", None, 0.0, synth_ms)
