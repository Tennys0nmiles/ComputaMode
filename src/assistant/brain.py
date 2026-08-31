"""Ollama-backed conversational brain.

Sends chat messages to a local Ollama instance.  Never executes system
actions — it only returns text.  Conversation history is capped to keep
small models from getting confused by long context.
"""

import json
import re
from collections import deque
from typing import Iterator, Optional

import requests

# Strip qwen3-style <think>...</think> reasoning blocks from responses
_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)
# Sentence boundary for streaming sentence splitting
_SENT_RE = re.compile(r'(?<=[.!?;])\s+')


def _strip_think(text: str) -> str:
    return _THINK_RE.sub('', text).strip()


class AssistantBrain:
    def __init__(
        self,
        model: str = "",  # always set from voice_commands.yaml; no default here
        base_url: str = "http://localhost:11434",
        max_history_turns: int = 4,
        temperature: float = 0.7,
        no_think: bool = False,
        persona: str = (
            "You are Nova, a helpful and concise female assistant. "
            "Answer in 1-2 sentences unless the user asks you to elaborate. "
            "Never describe or narrate computer actions — those are handled separately."
        ),
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.no_think = no_think
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

    def stream_sentences(self, user_text: str,
                         sysinfo_context: str = "") -> Iterator[str]:
        """Stream the LLM response as sentences complete.

        Yields each sentence as soon as it ends (. ! ? ;) so the TTS
        pipeline can start speaking before the full response is generated.
        Skips qwen3 <think>...</think> reasoning blocks transparently.
        Updates conversation history after the stream finishes.
        """
        system = self.persona
        if sysinfo_context:
            system += f"\n\nCurrent system info: {sysinfo_context}"

        messages = [{"role": "system", "content": system}]
        messages.extend(list(self._history))
        messages.append({"role": "user", "content": user_text})

        request_body: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": self.temperature},
        }

        full_reply = ""
        buf = ""        # accumulates tokens between sentence boundaries
        in_think = False

        try:
            with requests.post(
                f"{self.base_url}/api/chat",
                json=request_body,
                stream=True,
                timeout=60,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("done"):
                        break

                    token = chunk.get("message", {}).get("content", "")
                    if not token:
                        continue

                    # Skip <think>...</think> blocks token-by-token
                    if in_think:
                        if "</think>" in token:
                            _, _, token = token.partition("</think>")
                            in_think = False
                        else:
                            continue

                    if "<think>" in token:
                        before, _, rest = token.partition("<think>")
                        buf += before
                        full_reply += before
                        if "</think>" in rest:
                            _, _, token = rest.partition("</think>")
                        else:
                            in_think = True
                            token = ""

                    buf += token
                    full_reply += token

                    # Yield any complete sentences from the buffer
                    while True:
                        m = _SENT_RE.search(buf)
                        if not m:
                            break
                        sentence = buf[:m.end()].strip()
                        buf = buf[m.end():]
                        if sentence:
                            yield sentence

        except requests.exceptions.ConnectionError:
            yield "Sorry, I can't reach Ollama right now. Is the service running?"
            return
        except requests.exceptions.Timeout:
            yield "Sorry, that took too long. Try a shorter question."
            return
        except Exception as exc:
            print(f"[Brain] stream error: {exc}")
            yield "Sorry, something went wrong with the assistant."
            return

        # Flush any remaining text that didn't end with punctuation
        remainder = buf.strip()
        if remainder:
            yield remainder

        # Update rolling conversation history
        clean = full_reply.strip()
        if clean:
            self._history.append({"role": "user", "content": user_text})
            self._history.append({"role": "assistant", "content": clean})

    def clear_history(self) -> None:
        self._history.clear()
