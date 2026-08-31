"""Tests for src/voice/interpreter.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.voice.interpreter import Interpreter, MatchResult, _normalize
from src.voice.registry import (
    AppEntry, IntentEntry, ParsedPattern, Registry,
    SlotSpec, SlotType
)

MINIMAL_CONFIG = {
    "matching": {
        "v2_confidence_threshold": 0.75,
        "v2_margin_gap": 0.10,
        "tier3_timeout_seconds": 6,
        "filler_words": ["um", "uh", "please"],
    },
    "assistant": {
        "llm": {
            "model": "qwen3:4b",
            "base_url": "http://localhost:11434",
        }
    },
}


def make_registry(intents=None, apps=None):
    """Create a Registry-like mock wired to the given dicts."""
    intents_dict = intents or {}
    apps_dict = apps or {}

    reg = MagicMock(spec=Registry)

    def resolve_app(text):
        from src.voice.registry import parse_number
        text_lower = text.lower()
        best, best_len = None, 0
        for entry in apps_dict.values():
            for alias in entry.aliases:
                import re
                try:
                    if re.search(r'\b' + re.escape(alias.lower()) + r'\b', text_lower):
                        if len(alias) > best_len:
                            best_len = len(alias)
                            best = entry
                except re.error:
                    pass
        return best

    def resolve_number(text):
        from src.voice.registry import parse_number
        return parse_number(text)

    reg.resolve_app.side_effect = resolve_app
    reg.resolve_number.side_effect = resolve_number
    reg.get_intent.side_effect = lambda n: intents_dict.get(n)
    reg.all_intents.side_effect = lambda: list(intents_dict.values())
    reg.all_apps.side_effect = lambda: list(apps_dict.values())
    reg.to_llm_prompt_json.return_value = "[]"
    reg.load_embeddings.return_value = None
    return reg


def firefox_app():
    return AppEntry(
        name="firefox",
        aliases=["firefox", "browser"],
        verbs={"open": "firefox -P default", "close": "pkill firefox"},
        keywords=[],
        source="yaml",
    )


# ── Tier 1 ────────────────────────────────────────────────────────────────────

class TestTier1:
    def test_open_app_resolves(self):
        apps = {"firefox": firefox_app()}
        intents = {
            "open_app": IntentEntry(
                name="open_app",
                phrases=["open {app}"],
                action_template="{app.open}",
                pattern=ParsedPattern.from_string("open {app}"),
            )
        }
        reg = make_registry(intents, apps)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("open firefox")
        assert result is not None
        assert result.intent_name == "open_app"
        assert result.action == "firefox -P default"
        assert result.tier == "1"
        assert result.confidence == 1.0

    def test_set_volume_digit(self):
        intents = {
            "set_volume": IntentEntry(
                name="set_volume",
                phrases=["set volume to {number}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {number}%",
                pattern=ParsedPattern.from_string("set volume to {number}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("set volume to 50")
        assert result is not None
        assert "50" in result.action

    def test_set_volume_word_number(self):
        intents = {
            "set_volume": IntentEntry(
                name="set_volume",
                phrases=["set volume to {number}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {number}%",
                pattern=ParsedPattern.from_string("set volume to {number}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("set volume to forty two")
        assert result is not None
        assert "42" in result.action

    def test_type_text_greedy(self):
        intents = {
            "type_text": IntentEntry(
                name="type_text",
                phrases=["type {text}"],
                action_template="ydotool type --delay 100 -- {text}",
                pattern=ParsedPattern.from_string("type {text}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("type hello world foo bar")
        assert result is not None
        assert "hello world foo bar" in result.action

    def test_unresolvable_app_returns_none(self):
        apps = {}
        intents = {
            "open_app": IntentEntry(
                name="open_app",
                phrases=["open {app}"],
                action_template="{app.open}",
                pattern=ParsedPattern.from_string("open {app}"),
            )
        }
        reg = make_registry(intents, apps)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("open blorp")
        assert result is None

    def test_level_slot_maps_to_percentage(self):
        intents = {
            "set_volume_level": IntentEntry(
                name="set_volume_level",
                phrases=["set volume to {level}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {level}%",
                pattern=ParsedPattern.from_string("set volume to {level}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("set volume to 5")
        assert result is not None
        assert result.action == "wpctl set-volume @DEFAULT_AUDIO_SINK@ 50%"

    def test_level_slot_word_number(self):
        intents = {
            "set_volume_level": IntentEntry(
                name="set_volume_level",
                phrases=["set volume to {level}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {level}%",
                pattern=ParsedPattern.from_string("set volume to {level}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("set volume to seven")
        assert result is not None
        assert result.action == "wpctl set-volume @DEFAULT_AUDIO_SINK@ 70%"

    def test_level_slot_out_of_range_falls_through(self):
        """Numbers >10 must not match a {level} slot (no accidental 500%)."""
        intents = {
            "set_volume_level": IntentEntry(
                name="set_volume_level",
                phrases=["set volume to {level}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {level}%",
                pattern=ParsedPattern.from_string("set volume to {level}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("set volume to 50")
        assert result is None  # 50 > 10 → LEVEL rejects, no match

    def test_level_zero(self):
        intents = {
            "set_volume_level": IntentEntry(
                name="set_volume_level",
                phrases=["set volume to {level}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {level}%",
                pattern=ParsedPattern.from_string("set volume to {level}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("set volume to 0")
        assert result is not None
        assert result.action == "wpctl set-volume @DEFAULT_AUDIO_SINK@ 0%"

    def test_level_ten_is_100_percent(self):
        intents = {
            "set_volume_level": IntentEntry(
                name="set_volume_level",
                phrases=["set volume to {level}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {level}%",
                pattern=ParsedPattern.from_string("set volume to {level}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("set volume to 10")
        assert result is not None
        assert result.action == "wpctl set-volume @DEFAULT_AUDIO_SINK@ 100%"

    def test_explicit_percent_bypasses_level(self):
        """'set volume to 50 percent' should match the explicit-percent pattern."""
        intents = {
            "set_volume_level": IntentEntry(
                name="set_volume_level",
                phrases=["set volume to {level}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {level}%",
                pattern=ParsedPattern.from_string("set volume to {level}"),
            ),
            "set_volume": IntentEntry(
                name="set_volume",
                phrases=["set volume to {number} percent"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {number}%",
                pattern=ParsedPattern.from_string("set volume to {number} percent"),
            ),
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("set volume to 50 percent")
        assert result is not None
        assert result.intent_name == "set_volume"
        assert result.action == "wpctl set-volume @DEFAULT_AUDIO_SINK@ 50%"

    def test_wrong_prefix_returns_none(self):
        intents = {
            "set_volume": IntentEntry(
                name="set_volume",
                phrases=["set volume to {number}"],
                action_template="wpctl set-volume @DEFAULT_AUDIO_SINK@ {number}%",
                pattern=ParsedPattern.from_string("set volume to {number}"),
            )
        }
        reg = make_registry(intents)
        interp = Interpreter(reg, MINIMAL_CONFIG)
        result = interp._tier1_match("change volume to 50")
        assert result is None


# ── Tier 2 ────────────────────────────────────────────────────────────────────

class TestTier2:
    def _make_interp(self, intents=None, apps=None, threshold=0.75, margin=0.10):
        cfg = {
            "matching": {
                "v2_confidence_threshold": threshold,
                "v2_margin_gap": margin,
                "tier3_timeout_seconds": 6,
                "filler_words": ["um", "uh"],
            },
            "assistant": {"llm": {"model": "qwen3:4b", "base_url": "http://localhost:11434"}},
        }
        reg = make_registry(intents or {}, apps or {})
        interp = Interpreter(reg, cfg)
        # Bypass embedding cache: provide phrase lists directly
        interp._emb_loaded = True
        interp._embeddings = None
        interp._model = None
        all_phrases, all_intent_names = [], []
        for intent in (intents or {}).values():
            for phrase in intent.phrases:
                all_phrases.append(phrase.lower())
                all_intent_names.append(intent.name)
        interp._emb_phrases = all_phrases
        interp._emb_intent_names = all_intent_names
        return interp

    def test_high_score_matches(self):
        pytest.importorskip("rapidfuzz")
        intents = {
            "scroll_up": IntentEntry(
                name="scroll_up",
                phrases=["scroll up", "go up", "move up"],
                action_template="scroll_up",
            )
        }
        interp = self._make_interp(intents, threshold=0.5, margin=0.05)
        result = interp._tier2_match("scroll up")
        assert result is not None
        assert result.intent_name == "scroll_up"
        assert result.tier == "2"

    def test_below_threshold_returns_none(self):
        pytest.importorskip("rapidfuzz")
        intents = {
            "scroll_up": IntentEntry(
                name="scroll_up",
                phrases=["scroll up"],
                action_template="scroll_up",
            )
        }
        interp = self._make_interp(intents, threshold=0.99, margin=0.01)
        result = interp._tier2_match("totally unrelated utterance xyz")
        assert result is None

    def test_margin_too_small_returns_none(self):
        pytest.importorskip("rapidfuzz")
        intents = {
            "scroll_up": IntentEntry(
                name="scroll_up",
                phrases=["scroll up"],
                action_template="scroll_up",
            ),
            "scroll_down": IntentEntry(
                name="scroll_down",
                phrases=["scroll down"],
                action_template="scroll_down",
            ),
        }
        # With a huge margin gap requirement, ambiguous phrases should return None
        interp = self._make_interp(intents, threshold=0.5, margin=0.99)
        result = interp._tier2_match("scroll")
        assert result is None

    def test_unresolved_slot_sets_clarification(self):
        pytest.importorskip("rapidfuzz")
        intents = {
            "open_app": IntentEntry(
                name="open_app",
                phrases=["open firefox", "open browser"],
                action_template="{app.open}",
                pattern=ParsedPattern.from_string("open {app}"),
            )
        }
        interp = self._make_interp(intents, apps={}, threshold=0.5, margin=0.05)
        result = interp._tier2_match("open firefox")
        if result is not None:
            # No app registered → clarification should be set
            assert result.clarification_question is not None


# ── LLM Validation & Tier 3 ───────────────────────────────────────────────────

class TestLLMValidation:
    def _make_interp(self, intents=None, apps=None):
        return Interpreter(make_registry(intents or {}, apps or {}), MINIMAL_CONFIG)

    def test_valid_intent_no_slots(self):
        intents = {
            "scroll_up": IntentEntry(
                name="scroll_up",
                phrases=["scroll up"],
                action_template="scroll_up",
            )
        }
        interp = self._make_interp(intents)
        assert interp._validate_llm_args(intents["scroll_up"], {}) is True

    def test_unknown_returns_none(self):
        interp = self._make_interp()
        with patch.object(interp, '_call_llm_json', return_value={"intent": "unknown"}):
            result = interp._tier3_match("what is the weather")
        assert result is None

    def test_hallucinated_intent_returns_error(self):
        interp = self._make_interp()
        with patch.object(interp, '_call_llm_json',
                          return_value={"intent": "fake_intent_xyz_not_real"}):
            result = interp._tier3_match("do something")
        assert result is not None
        assert result.intent_name == "_error_"

    def test_malformed_json_returns_error(self):
        interp = self._make_interp()
        with patch.object(interp, '_call_llm_json', return_value=None):
            result = interp._tier3_match("some command")
        assert result is not None
        assert result.intent_name == "_error_"

    def test_timeout_returns_error(self):
        interp = self._make_interp()
        with patch.object(interp, '_call_llm_json', return_value=None):
            result = interp._tier3_match("some voice command")
        assert result is not None
        assert result.intent_name == "_error_"

    def test_invalid_app_arg_returns_error(self):
        intents = {
            "open_app": IntentEntry(
                name="open_app",
                phrases=["open {app}"],
                action_template="{app.open}",
                pattern=ParsedPattern.from_string("open {app}"),
            )
        }
        apps = {}  # no apps registered → resolve_app returns None
        interp = self._make_interp(intents, apps)
        intent = intents["open_app"]
        assert interp._validate_llm_args(intent, {"app": "nonexistent_app_xyz"}) is False

    def test_think_block_stripped(self):
        interp = self._make_interp()
        response_with_think = '<think>Let me reason...</think>{"intent": "unknown"}'
        with patch('requests.post') as mock_post:
            mock_post.return_value.json.return_value = {
                "message": {"content": response_with_think}
            }
            mock_post.return_value.raise_for_status = MagicMock()
            result = interp._call_llm_json("system prompt", "user text")
        assert result == {"intent": "unknown"}
