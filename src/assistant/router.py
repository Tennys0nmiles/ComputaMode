"""Conversational router: sysinfo fast path → LLM path → TTS output.

Called by fallback_interpreter() in intent.py when transcribed speech
doesn't match any Stage 2 command intent.  The router NEVER executes
system actions — it only speaks answers back to the user.

Fast path (no LLM):
  Regex patterns catch common system-info questions and answer instantly
  from psutil data — battery, time, date, CPU, RAM, disk, uptime.

LLM path:
  Everything else goes to the local Ollama brain with sysinfo injected
  as context.  The LLM's only power is to generate spoken text.
"""

import re
import threading
from typing import Optional, TYPE_CHECKING

from src.assistant import sysinfo

if TYPE_CHECKING:
    from src.assistant.tts import TTSEngine
    from src.assistant.brain import AssistantBrain


# ── Sysinfo fast-path patterns ─────────────────────────────────────────────
# Each entry: (compiled regex, answer-getter callable)
_FAST_PATH = [
    (re.compile(r'\b(battery|charge|charging|power)\b'),
     lambda: f"Battery is {sysinfo.battery()}."),
    (re.compile(r'\b(time|clock|hour)\b'),
     lambda: f"It's {sysinfo.current_time()}."),
    (re.compile(r'\b(date|day|today|weekday)\b'),
     lambda: f"Today is {sysinfo.current_date()}."),
    (re.compile(r'\b(cpu|processor|load|usage)\b'),
     lambda: f"CPU usage is {sysinfo.cpu()}."),
    (re.compile(r'\b(ram|memory|mem)\b'),
     lambda: f"RAM: {sysinfo.ram()}."),
    (re.compile(r'\b(disk|storage|space|drive)\b'),
     lambda: f"Disk: {sysinfo.disk()}."),
    (re.compile(r'\b(uptime|running|started|rebooted)\b'),
     lambda: f"System has been running for {sysinfo.uptime()}."),
    (re.compile(r'\b(network|internet|wifi|connected|online)\b'),
     lambda: "Network is connected." if sysinfo.network_up() else "Network appears disconnected."),
]


class ConversationRouter:
    """Routes un-matched speech to sysinfo fast path or LLM, then speaks."""

    def __init__(self, brain: "AssistantBrain", tts: "TTSEngine"):
        self._brain = brain
        self._tts = tts
        self._busy = threading.Event()  # set while an LLM call is in-flight

    def handle(self, text: str) -> None:
        """Decide fast-path or LLM and speak the answer. Never raises."""
        text_lower = text.lower()

        # Fast path: sysinfo patterns — no LLM needed, always runs immediately
        for pattern, getter in _FAST_PATH:
            if pattern.search(text_lower):
                try:
                    answer = getter()
                    self._tts.speak(answer)
                except Exception as exc:
                    print(f"[Router] sysinfo error: {exc}")
                return

        # LLM path — drop if already processing to avoid stacking responses
        if self._busy.is_set():
            print("[Router] already processing, dropping duplicate request")
            return

        self._busy.set()
        print("[Nova] ...", flush=True)
        try:
            context = sysinfo.get_context_string()
            sentences = self._brain.stream_sentences(text, sysinfo_context=context)
            self._tts.speak_iter(sentences)
        except Exception as exc:
            print(f"[Router] LLM error: {exc}", flush=True)
        finally:
            self._busy.clear()


# Module-level singleton set by main.py during startup
_router: Optional["ConversationRouter"] = None


def init(brain: "AssistantBrain", tts: "TTSEngine") -> None:
    global _router
    _router = ConversationRouter(brain, tts)


def handle_conversational(text: str) -> None:
    """Entry point called from fallback_interpreter(). Thread-safe."""
    if _router is None:
        return
    _router.handle(text)
