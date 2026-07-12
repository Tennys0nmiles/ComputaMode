"""Ollama-backed conversational brain.

Sends chat messages to a local Ollama instance.  Never executes system
actions — it only returns text.  Conversation history is capped to keep
small models from getting confused by long context.
"""

import re
from collections import deque
from typing import Optional

import requests

# Strip qwen3-style <think>...</think> reasoning blocks from responses
_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)


def _strip_think(text: str) -> str:
    return _THINK_RE.sub('', text).strip()


class AssistantBrain:
    def __init__(
        self,
        model: str = "",  # always set from voice_commands.yaml; no default here
        base_url: str = "http://localhost:11434",
        max_history_turns: int = 4,
        temperature: float = 0.7,
        persona: str = (
            "You are Nova, a helpful and concise female assistant. "
            "Answer in 1-2 sentences unless the user asks you to elaborate. "
            "Never describe or narrate computer actions — those are handled separately."
        ),
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.persona = persona
        # deque holds alternating user/assistant dicts; max_history_turns * 2
        self._history: deque = deque(maxlen=max_history_turns * 2)

    def ask(self, user_text: str, sysinfo_context: str = "") -> str:
        """Send user_text to the LLM and return the response string.

        sysinfo_context is injected into the system prompt so the model
        can reference it when the user asks about battery, time, etc.
        Does NOT give the LLM any ability to run commands.
        """
        system = self.persona
        if sysinfo_context:
            system += f"\n\nCurrent system info: {sysinfo_context}"

        messages = [{"role": "system", "content": system}]
        messages.extend(list(self._history))
        messages.append({"role": "user", "content": user_text})

        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": self.temperature},
                },
                timeout=45,
            )
            resp.raise_for_status()
            reply = _strip_think(resp.json()["message"]["content"])
        except requests.exceptions.ConnectionError:
            return "Sorry, I can't reach Ollama right now. Is the service running?"
        except requests.exceptions.Timeout:
            return "Sorry, that took too long. Try a shorter question."
        except Exception as exc:
            print(f"[Brain] error: {exc}")
            return "Sorry, something went wrong with the assistant."

        # Update rolling history
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": reply})

        return reply

    def clear_history(self) -> None:
        self._history.clear()
