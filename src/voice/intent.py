"""Intent matching: map transcribed text to a configured action.

Matching strategy (multi-signal, best-of):
  1. Character SequenceMatcher ratio  — catches phonetic similarity
  2. Word overlap ratio               — handles extra/missing words,
                                        articles, punctuation, politeness
  3. Substring bonus                  — exact phrase appears in transcription

Word overlap is the most robust signal for voice commands because Whisper
frequently adds surrounding words ("can you please", trailing periods, "the",
"a", etc.) that tanking character similarity while leaving all command
words intact.

Fallback hook:
    fallback_interpreter(text) is called when nothing matches confidently.
    Routes to the Stage 3 conversational assistant (Ollama + TTS).
"""

import difflib
import re
import shlex
import threading
from typing import List, Optional, Set, Tuple

import yaml

# Strip punctuation before any matching — Whisper always adds periods,
# commas, question marks, etc. to clean audio.
_PUNCT_RE = re.compile(r"[^\w\s]")


def _clean(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return _PUNCT_RE.sub("", text.lower()).strip()


def _word_set(text: str) -> Set[str]:
    return set(text.split())


def _word_overlap(query_words: Set[str], phrase_words: Set[str]) -> float:
    """Fraction of phrase words found in query. Handles extra query words."""
    if not phrase_words:
        return 0.0
    return len(phrase_words & query_words) / len(phrase_words)


def _score(query: str, phrase: str) -> float:
    """Best-of three matching signals."""
    # 1. Character similarity
    char_score = difflib.SequenceMatcher(None, query, phrase).ratio()
    # 2. Word overlap — immune to extra words, articles, punctuation
    word_score = _word_overlap(_word_set(query), _word_set(phrase))
    # 3. Substring — phrase appears verbatim inside transcription
    sub_score = 0.93 if phrase in query else 0.0
    return max(char_score, word_score, sub_score)


def _strip_fillers(text: str, fillers: set) -> str:
    """Remove filler words from the start/end of the transcription."""
    words = text.split()
    while words and words[0] in fillers:
        words.pop(0)
    while words and words[-1] in fillers:
        words.pop()
    return " ".join(words)


def fallback_interpreter(text: str) -> Optional[str]:
    """Route unmatched speech to the Stage 3 conversational assistant.

    Fire-and-forget: spawns a daemon thread for the LLM call and returns
    immediately, so the calling voice-task thread is freed at once.
    This means subsequent PTT commands can transcribe and execute without
    waiting for Ollama to respond.

    Returns None — the assistant speaks via TTS as a side effect only,
    it never triggers system actions.
    """
    def _run():
        try:
            from src.assistant.router import handle_conversational
            handle_conversational(text)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return None


class IntentMatcher:
    """Matches transcribed text to intents defined in voice_commands.yaml."""

    def __init__(self, config_path: str):
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        self.intents = cfg.get("intents", {})
        match_cfg = cfg.get("matching", {})
        self.threshold = match_cfg.get("confidence_threshold", 0.55)
        self.strip_fillers_flag = match_cfg.get("strip_fillers", True)
        self.fillers = set(match_cfg.get("filler_words", []))

    def _score_against_all(self, text: str) -> Tuple[Optional[str], float, str]:
        """
        Score cleaned text against every phrase in every intent.
        Returns (action, score, matched_phrase) of the best match,
        or (None, best_score, best_phrase) if below threshold.
        """
        cleaned = _clean(text)
        if self.strip_fillers_flag:
            cleaned = _strip_fillers(cleaned, self.fillers)
        if not cleaned:
            return None, 0.0, ""

        best_score = 0.0
        best_action = None
        best_phrase = ""

        for intent_cfg in self.intents.values():
            action = intent_cfg.get("action", "")
            for phrase in intent_cfg.get("phrases", []):
                s = _score(cleaned, _clean(phrase))
                if s > best_score:
                    best_score = s
                    best_action = action
                    best_phrase = phrase

        if best_score >= self.threshold:
            return best_action, best_score, best_phrase
        return None, best_score, best_phrase

    def _match_no_fallback(self, text: str) -> Tuple[Optional[str], float, str]:
        return self._score_against_all(text)

    def match(self, text: str) -> Tuple[Optional[str], float, str]:
        """Match text to the best intent, calling fallback if nothing matches."""
        if not text:
            return None, 0.0, ""
        action, score, phrase = self._score_against_all(text)
        if action:
            return action, score, phrase
        fallback = fallback_interpreter(text)
        if fallback:
            return fallback, 1.0, "(fallback)"
        return None, score, phrase

    # Dictation trigger — checked before chain splitting so
    # "write hello and goodbye" types the full phrase as-is.
    _DICTATE_PREFIX = re.compile(
        r'^(?:write|right|type|dictate|input)\s+(.+)$', re.IGNORECASE
    )

    # Words that join chained commands
    _CHAIN_SPLIT = re.compile(
        r'\b(?:and then|after that|and also|and|then|also|next)\b'
    )

    def match_all(self, text: str) -> List[Tuple[Optional[str], float, str]]:
        """Detect dictation, then split chains and match each segment.

        Dictation: starts with write/right/type/dictate → type verbatim
          via ydotool (native Wayland; xdotool type doesn't reach Wayland apps).
        Commands: split on and/then/also, match each segment independently.
          Only sends full text to assistant if NOTHING matched at all.
        """
        text = text.strip()

        # Dictation takes priority
        m = self._DICTATE_PREFIX.match(text)
        if m:
            content = m.group(1).strip()
            if content:
                action = f"ydotool type --delay 100 -- {shlex.quote(content)}"
                return [(action, 1.0, f"dictate: {content}")]
            return []

        segments = [s.strip() for s in self._CHAIN_SPLIT.split(text) if s.strip()]
        if not segments:
            segments = [text]

        results = []
        any_matched = False
        for seg in segments:
            action, score, phrase = self._match_no_fallback(seg)
            if action:
                results.append((action, score, phrase))
                any_matched = True

        if not any_matched:
            fallback = fallback_interpreter(text)
            if fallback:
                results.append((fallback, 1.0, "(fallback)"))

        return results
