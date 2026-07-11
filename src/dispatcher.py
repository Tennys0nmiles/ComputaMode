"""Action dispatcher: shared by gesture and voice command layers.

Maps action strings (from voice_commands.yaml or config.yaml) to concrete
operations on the virtual mouse and app state.

Built-in action keywords:
    scroll_up, scroll_down        — mouse wheel
    left_click, right_click       — mouse buttons
    pause_gestures                — freeze gesture control
    resume_gestures               — unfreeze gesture control

Anything else is executed as a shell command via subprocess.
"""

import subprocess

from evdev import ecodes


class ActionDispatcher:
    """Executes action strings from the intent matcher or config."""

    BUILT_IN = frozenset({
        "scroll_up", "scroll_down",
        "left_click", "right_click",
        "pause_gestures", "resume_gestures",
    })

    def __init__(self, mouse, state: dict):
        """
        Args:
            mouse: VirtualMouse instance.
            state: mutable dict; must contain key 'paused' (bool).
        """
        self.mouse = mouse
        self.state = state

    def execute(self, action: str) -> bool:
        """Execute an action string. Returns True if the action was handled."""
        if not action:
            return False

        if action == "scroll_up":
            self.mouse.scroll(3)
        elif action == "scroll_down":
            self.mouse.scroll(-3)
        elif action == "left_click":
            self.mouse.click()
        elif action == "right_click":
            self.mouse.click(ecodes.BTN_RIGHT)
        elif action == "pause_gestures":
            self.state["paused"] = True
        elif action == "resume_gestures":
            self.state["paused"] = False
        else:
            try:
                subprocess.Popen(
                    action, shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                return False
        return True
