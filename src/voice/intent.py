"""Intent matching: map transcribed text to a configured action.

Uses fuzzy string matching (stdlib difflib) so minor transcription errors
("scroll op" → "scroll up") still match. No LLM, no network.

Fallback hook:
    fallback_interpreter(text) is called when nothing matches confidently.
    Currently a no-op — replace with a local Ollama call later if desired.
"""

import difflib
import re
import shlex
from typing import List, Optional, Tuple

import yaml


def _load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _strip_fillers(text: str, fillers: list) -> str:
    """Remove filler words from the start/end of the transcription."""
    words = text.split()
    while words and words[0] in fillers:
        words.pop(0)
    while words and words[-1] in fillers:
        words.pop()
    return " ".join(words)


def _similarity(a: str, b: str) -> float:
    """Normalized similarity score between two strings."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def fallback_interpreter(text: str) -> Optional[str]:
    """Route unmatched speech to the Stage 3 conversational assistant.

    Speaks a response via TTS as a side effect (runs in the background
    voice-task thread so the gesture loop is never blocked).
    Returns None so the dispatcher executes nothing — the assistant only
    talks, it never triggers system actions.

    Degrades gracefully: if Stage 3 isn't installed or Ollama is down,
    this silently returns None and Stage 2 command handling is unaffected.
    """
    try:
        from src.assistant.router import handle_conversational
        handle_conversational(text)
    except Exception:
        pass
    return None


class IntentMatcher:
    """Matches transcribed text to intents defined in voice_commands.yaml."""

    def __init__(self, config_path: str):
        cfg = _load_config(config_path)
        self.intents = cfg.get("intents", {})
        match_cfg = cfg.get("matching", {})
        self.threshold = match_cfg.get("confidence_threshold", 0.6)
        self.strip_fillers = match_cfg.get("strip_fillers", True)
        self.fillers = set(match_cfg.get("filler_words", []))

    def match(self, text: str) -> Tuple[Optional[str], float, str]:
        """Match text to the best intent.

        Returns:
            (action, score, matched_phrase) if confident match found.
            (None, best_score, best_phrase) if below threshold.
        """
        if not text:
            return None, 0.0, ""

        text = text.strip().lower()
        if self.strip_fillers:
            text = _strip_fillers(text, self.fillers)

        best_score = 0.0
        best_action = None
        best_phrase = ""
        best_intent = ""

        for intent_name, intent_cfg in self.intents.items():
            phrases = intent_cfg.get("phrases", [])
            action = intent_cfg.get("action", "")
            for phrase in phrases:
                score = _similarity(text, phrase.lower())
                if score > best_score:
                    best_score = score
                    best_action = action
                    best_phrase = phrase
                    best_intent = intent_name

        if best_score >= self.threshold:
            return best_action, best_score, best_phrase

        # Nothing matched — try fallback hook
        fallback = fallback_interpreter(text)
        if fallback:
            return fallback, 1.0, "(fallback)"

        return None, best_score, best_phrase

    # Dictation trigger: "write ..." / "type ..." → type the remainder verbatim
    # Checked before chain splitting so "write hello and goodbye" types the full phrase.
    _DICTATE_PREFIX = re.compile(
        r'^(?:write|type|dictate|input)\s+(.+)$', re.IGNORECASE
    )

    # Words that join chained commands: "close tab and open terminal"
    _CHAIN_SPLIT = re.compile(
        r'\b(?:and then|after that|and also|and|then|also|next)\b'
    )

    def match_all(self, text: str) -> List[Tuple[Optional[str], float, str]]:
        """Detect dictation or split on chain words and match each segment.

        Dictation mode: if text starts with "write"/"type"/"dictate"/"input",
        the remainder is typed verbatim at the cursor via xdotool type.
        This bypasses intent matching and chain splitting entirely so that
        "write hello and goodbye" types "hello and goodbye" as-is.

        Command mode: split on "and/then/also/after that" and match each
        segment against the intent list. Unmatched segments are skipped.

        Returns a list of (action, score, phrase).
        """
        text = text.strip()

        # Dictation takes priority over everything else
        m = self._DICTATE_PREFIX.match(text)
        if m:
            content = m.group(1).strip()
            if content:
                action = f"xdotool type --clearmodifiers -- {shlex.quote(content)}"
                return [(action, 1.0, f"dictate: {content}")]
            return []

        segments = [s.strip() for s in self._CHAIN_SPLIT.split(text) if s.strip()]
        if not segments:
            segments = [text]

        results = []
        for seg in segments:
            action, score, phrase = self.match(seg)
            if action:
                results.append((action, score, phrase))

        return results
