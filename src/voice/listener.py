"""Push-to-talk microphone recorder.

Non-blocking: call start_recording() when PTT activates, stop_recording()
when released. Returns audio resampled to 16000 Hz as a numpy int16 array.

Records at the device's native sample rate (44100 Hz on most hardware) to
avoid PipeWire/ALSA doing a broken internal resample at 16000 Hz, which
produces corrupted audio. Resampling is done here with scipy after capture.
"""

import math
import threading
import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

# Whisper requires 16 kHz; we always return at this rate
WHISPER_RATE = 16000
CHANNELS = 1
MAX_DURATION = 8.0  # hard cap to prevent runaway recordings


def _native_rate() -> int:
    """Return the default input device's native sample rate."""
    try:
        info = sd.query_devices(kind="input")
        return int(info["default_samplerate"])
    except Exception:
        return 44100


class PTTRecorder:
    """Records audio between start_recording() and stop_recording() calls.

    Always returns 16000 Hz int16 audio regardless of hardware sample rate.
    """

    def __init__(self):
        self._capture_rate = _native_rate()
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self._recording = False
        # Precompute resample ratio
        g = math.gcd(WHISPER_RATE, self._capture_rate)
        self._up = WHISPER_RATE // g
        self._down = self._capture_rate // g

    @property
    def sample_rate(self) -> int:
        """Output sample rate (always WHISPER_RATE)."""
        return WHISPER_RATE

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self):
        """Begin capturing audio. Non-blocking."""
        with self._lock:
            if self._recording:
                return
            self._chunks = []
            self._recording = True
            self._stream = sd.InputStream(
                samplerate=self._capture_rate,
                channels=CHANNELS,
                dtype="int16",
                blocksize=1024,
                callback=self._callback,
            )
            self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            self._chunks.append(indata.copy())

    def stop_recording(self) -> np.ndarray:
        """Stop capturing and return 16 kHz int16 audio."""
        with self._lock:
            if not self._recording:
                return np.array([], dtype=np.int16)
            self._recording = False
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None

        if not self._chunks:
            return np.array([], dtype=np.int16)

        raw = np.concatenate(self._chunks, axis=0).flatten()

        # Resample from hardware rate → 16 kHz for Whisper
        if self._capture_rate != WHISPER_RATE:
            resampled = resample_poly(raw.astype(np.float32), self._up, self._down)
            raw = np.clip(resampled, -32768, 32767).astype(np.int16)

        return raw
