"""Push-to-talk microphone recorder.

Non-blocking: call start_recording() when PTT activates, stop_recording()
when released. Returns the captured audio as a numpy array.
"""

import threading
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
MAX_DURATION = 8.0  # hard cap to prevent runaway recordings


class PTTRecorder:
    """Records audio between start_recording() and stop_recording() calls."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self._recording = False

    @property
    def is_recording(self):
        return self._recording

    def start_recording(self):
        """Begin capturing audio. Non-blocking."""
        with self._lock:
            if self._recording:
                return
            self._chunks = []
            self._recording = True
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
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
        """Stop capturing and return the recorded audio as int16 numpy array."""
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

        audio = np.concatenate(self._chunks, axis=0).flatten()
        return audio
