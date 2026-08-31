"""V2 action executor: replaces dispatcher.py for the v2 interpreter path."""

import os
import re
import shlex
import subprocess
import threading
from typing import Optional

try:
    from evdev import ecodes as _ecodes
    BTN_RIGHT = _ecodes.BTN_RIGHT
except ImportError:
    # evdev not available (e.g. in test environments)
    _ecodes = None
    BTN_RIGHT = 0x111  # BTN_RIGHT value

from src.voice.interpreter import MatchResult

_SUBPROCESS_ENV = {
    **os.environ,
    "DISPLAY": os.environ.get("DISPLAY", ":0"),
    "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", "wayland-0"),
    "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
}

BUILT_IN = frozenset({
    "scroll_up", "scroll_down",
    "left_click", "right_click",
    "pause_gestures", "resume_gestures",
})


class Executor:
    def __init__(self, mouse, state: dict, tts=None, dry_run: bool = False):
        """
        Args:
            mouse:     VirtualMouse instance.
            state:     mutable dict; must contain key 'paused' (bool).
            tts:       TTSEngine instance for confirmation prompts (optional).
            dry_run:   If True, print commands instead of executing them.
        """
        self.mouse = mouse
        self.state = state
        self.tts = tts
        self.dry_run = dry_run
        self._pending_confirm: Optional[threading.Event] = None
        self._confirm_text: Optional[str] = None

    def execute(self, result: MatchResult) -> bool:
        """Execute a MatchResult. Returns True if action was handled."""
        if not result.action:
            return False

        if result.needs_confirm:
            if not self._confirmation_gate(result):
                return False

        if self.dry_run:
            print(f"[dry-run] tier={result.tier} intent={result.intent_name}: {result.action}")
            return True

        if result.action in BUILT_IN:
            return self._execute_builtin(result.action)
        return self._execute_shell(result.action)

    def _confirmation_gate(self, result: MatchResult) -> bool:
        """Speak confirmation request and wait up to 10 seconds for yes/no."""
        if self.tts:
            self.tts.speak(f"Say yes to confirm: {result.action}")
        event = threading.Event()
        self._pending_confirm = event
        self._confirm_text = None
        confirmed = event.wait(timeout=10)
        self._pending_confirm = None
        if not confirmed:
            return False
        text = (self._confirm_text or "").lower().strip()
        return bool(re.search(r'\b(?:yes|yeah|confirm|do it)\b', text))

    def confirm_response(self, text: str) -> None:
        """Called by voice_task when a confirmation response is heard."""
        self._confirm_text = text
        if self._pending_confirm is not None:
            self._pending_confirm.set()

    def _execute_builtin(self, action: str) -> bool:
        if action == "scroll_up":
            self.mouse.scroll(3)
        elif action == "scroll_down":
            self.mouse.scroll(-3)
        elif action == "left_click":
            self.mouse.click()
        elif action == "right_click":
            self.mouse.click(BTN_RIGHT)
        elif action == "pause_gestures":
            self.state["paused"] = True
        elif action == "resume_gestures":
            self.state["paused"] = False
        else:
            return False
        return True

    def _execute_shell(self, command: str) -> bool:
        try:
            parts = shlex.split(command)
        except ValueError:
            return False
        if not parts:
            return False
        try:
            subprocess.Popen(
                parts,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_SUBPROCESS_ENV,
            )
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False
