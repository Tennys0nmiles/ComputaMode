"""JSONL command logger for voice command coverage tracking.

Logs every utterance (both legacy and v2) to ~/.local/share/compu-mode/commands.jsonl
for post-hoc analysis of which tier handles which commands.
"""

import json
import queue
import threading
import time
from pathlib import Path
from typing import Optional

_LOG_DIR = Path("~/.local/share/compu-mode").expanduser()
_LOG_FILE = _LOG_DIR / "commands.jsonl"


class CommandLogger:
    """Non-blocking JSONL logger backed by a writer thread."""

    def __init__(self, log_path: Path = _LOG_FILE):
        self._log_path = log_path
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()

    def log(
        self,
        utterance: str,
        tier: str,
        intent: Optional[str],
        confidence: float,
        latency_ms: int,
        dry_run: bool = False,
        error: Optional[str] = None,
    ) -> None:
        """Enqueue a log entry (non-blocking)."""
        entry = {
            "ts": time.time(),
            "utterance": utterance,
            "tier": tier,
            "intent": intent,
            "confidence": round(confidence, 4),
            "latency_ms": latency_ms,
        }
        if dry_run:
            entry["dry_run"] = True
        if error:
            entry["error"] = error
        self._queue.put(entry)

    def close(self) -> None:
        """Flush and stop the writer thread."""
        self._queue.put(None)  # sentinel
        self._thread.join(timeout=5)

    def _writer(self) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a") as f:
            while True:
                entry = self._queue.get()
                if entry is None:
                    break
                f.write(json.dumps(entry) + "\n")
                f.flush()


_logger: Optional[CommandLogger] = None
_logger_lock = threading.Lock()


def get_logger() -> CommandLogger:
    """Return the module-level singleton logger."""
    global _logger
    with _logger_lock:
        if _logger is None:
            _logger = CommandLogger()
    return _logger
