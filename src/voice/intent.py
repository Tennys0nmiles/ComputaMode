"""Intent matching: map transcribed text to a configured action.

Uses fuzzy string matching (stdlib difflib) so minor transcription errors
("scroll op" → "scroll up") still match. No LLM, no network.

Fallback hook:
    fallback_interpreter(text) is called when nothing matches confidently.
    Currently a no-op — replace with a local Ollama call later if desired.
"""

import difflib
import re
from typing import Optional, Tuple

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
    """No-op fallback for unmatched commands.

    Return an action string to execute, or None to do nothing.
    Replace this function body with an Ollama call when ready.
    """
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
