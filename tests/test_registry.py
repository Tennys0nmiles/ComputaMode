"""Tests for src/voice/registry.py."""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.voice.registry import (
    AppEntry, IntentEntry, ParsedPattern, Registry,
    SlotSpec, SlotType, parse_number
)

MINIMAL_YAML = """\
apps:
  firefox:
    aliases: [firefox, browser, web]
    open: "firefox -P default"
    close: "pkill -SIGTERM firefox"
  terminal:
    aliases: [terminal, console]
    open: "gnome-terminal"

verbs:
  open: [open, launch]
  close: [close, quit]

intents:
  scroll_up:
    phrases: [scroll up, go up, move up]
    action: scroll_up
  set_volume:
    phrases: ["set volume to {number} percent", "volume {number}"]
    pattern: "set volume to {number}"
    action: "wpctl set-volume @DEFAULT_AUDIO_SINK@ {number}%"
  open_app:
    phrases: ["open {app}", "launch {app}"]
    pattern: "open {app}"
    action: "{app.open}"
  type_text:
    phrases: ["type {text}"]
    pattern: "type {text}"
    action: "ydotool type --delay 100 -- {text}"

matching:
  v2_confidence_threshold: 0.75
  v2_margin_gap: 0.10
  tier3_timeout_seconds: 6
"""


@pytest.fixture
def registry(tmp_path):
    cfg_file = tmp_path / "voice_commands.yaml"
    cfg_file.write_text(MINIMAL_YAML)
    with patch.object(Registry, '_load_apps_from_desktop', return_value={}):
        return Registry(str(cfg_file))


# ── Slot parsing ──────────────────────────────────────────────────────────────

class TestSlotParsing:
    def test_simple_number_slot(self):
        p = ParsedPattern.from_string("set volume to {number}")
        assert len(p.segments) == 2
        assert p.segments[0] == "set volume to"
        assert isinstance(p.segments[1], SlotSpec)
        assert p.segments[1].slot_type == SlotType.NUMBER

    def test_prefix_suffix_extraction(self):
        p = ParsedPattern.from_string("set volume to {number} percent")
        segs = p.segments
        assert segs[0] == "set volume to"
        assert isinstance(segs[1], SlotSpec)
        assert segs[2] == "percent"

    def test_multi_slot(self):
        p = ParsedPattern.from_string("move {app} to workspace {number}")
        segs = p.segments
        assert segs[0] == "move"
        assert isinstance(segs[1], SlotSpec)
        assert segs[1].slot_type == SlotType.APP
        assert segs[2] == "to workspace"
        assert isinstance(segs[3], SlotSpec)
        assert segs[3].slot_type == SlotType.NUMBER

    def test_text_slot_greedy(self):
        p = ParsedPattern.from_string("type {text}")
        assert len(p.segments) == 2
        assert p.segments[0] == "type"
        assert isinstance(p.segments[1], SlotSpec)
        assert p.segments[1].slot_type == SlotType.TEXT

    def test_level_slot_recognized(self):
        p = ParsedPattern.from_string("set volume to {level}")
        assert len(p.segments) == 2
        assert isinstance(p.segments[1], SlotSpec)
        assert p.segments[1].slot_type == SlotType.LEVEL

    def test_no_slots_is_single_literal(self):
        p = ParsedPattern.from_string("scroll up")
        assert p.segments == ["scroll up"]

    def test_raw_preserved(self):
        raw = "open {app}"
        p = ParsedPattern.from_string(raw)
        assert p.raw == raw


# ── App alias resolution ──────────────────────────────────────────────────────

class TestAppAliasResolution:
    def test_primary_alias(self, registry):
        result = registry.resolve_app("firefox")
        assert result is not None
        assert result.name == "firefox"

    def test_secondary_alias(self, registry):
        result = registry.resolve_app("browser")
        assert result is not None
        assert result.name == "firefox"

    def test_longest_match_wins(self, registry):
        # "browser" (7 chars) beats "web" (3 chars) — both match firefox
        result = registry.resolve_app("open the browser please")
        assert result is not None
        assert result.name == "firefox"

    def test_unknown_returns_none(self, registry):
        result = registry.resolve_app("blorp")
        assert result is None

    def test_word_boundary_match(self, registry):
        # "firefox" appears as a word — should match
        result = registry.resolve_app("firefox launcher")
        assert result is not None
        assert result.name == "firefox"


# ── Desktop file parsing ──────────────────────────────────────────────────────

class TestDesktopParsing:
    def _make_desktop_dir(self, tmp_path, files):
        d = tmp_path / "applications"
        d.mkdir(exist_ok=True)
        for fname, content in files.items():
            (d / fname).write_text(content)
        return d

    def test_strip_exec_placeholders(self, tmp_path):
        d = self._make_desktop_dir(tmp_path, {
            "myapp.desktop": (
                "[Desktop Entry]\nType=Application\nName=MyApp\nExec=myapp %U\n"
            )
        })
        reg = Registry.__new__(Registry)
        with patch.object(Registry, 'DESKTOP_DIRS', [d]):
            result = reg._load_apps_from_desktop()
        assert "myapp" in result
        assert result["myapp"].verbs["open"] == "myapp"

    def test_skip_nodisplay(self, tmp_path):
        d = self._make_desktop_dir(tmp_path, {
            "hidden.desktop": (
                "[Desktop Entry]\nType=Application\nName=Hidden\n"
                "Exec=hidden\nNoDisplay=true\n"
            )
        })
        reg = Registry.__new__(Registry)
        with patch.object(Registry, 'DESKTOP_DIRS', [d]):
            result = reg._load_apps_from_desktop()
        assert "hidden" not in result

    def test_skip_non_application(self, tmp_path):
        d = self._make_desktop_dir(tmp_path, {
            "link.desktop": (
                "[Desktop Entry]\nType=Link\nName=Link\nURL=http://example.com\n"
            )
        })
        reg = Registry.__new__(Registry)
        with patch.object(Registry, 'DESKTOP_DIRS', [d]):
            result = reg._load_apps_from_desktop()
        assert "link" not in result

    def test_keywords_extracted(self, tmp_path):
        d = self._make_desktop_dir(tmp_path, {
            "calc.desktop": (
                "[Desktop Entry]\nType=Application\nName=Calculator\n"
                "Exec=gnome-calculator\nKeywords=math;arithmetic;\n"
            )
        })
        reg = Registry.__new__(Registry)
        with patch.object(Registry, 'DESKTOP_DIRS', [d]):
            result = reg._load_apps_from_desktop()
        assert "calculator" in result
        assert "math" in result["calculator"].keywords

    def test_yaml_overrides_desktop(self, tmp_path):
        cfg_content = """\
apps:
  firefox:
    aliases: [firefox, browser]
    open: "firefox -P default"
matching: {}
intents: {}
"""
        cfg_file = tmp_path / "voice_commands.yaml"
        cfg_file.write_text(cfg_content)
        d = self._make_desktop_dir(tmp_path, {
            "firefox.desktop": (
                "[Desktop Entry]\nType=Application\nName=firefox\nExec=firefox\n"
            )
        })
        with patch.object(Registry, 'DESKTOP_DIRS', [d]):
            reg = Registry(str(cfg_file))
        assert reg._apps["firefox"].source == "yaml"


# ── Number parsing ────────────────────────────────────────────────────────────

class TestNumberParsing:
    @pytest.mark.parametrize("text,expected", [
        ("42", 42),
        ("forty two", 42),
        ("forty-two", 42),
        ("volume forty percent", 40),
        ("nineteen", 19),
        ("blorp", None),
        ("100", 100),
        ("twenty", 20),
        ("0", 0),
    ])
    def test_parse_number(self, text, expected):
        assert parse_number(text) == expected


# ── Embedding cache hash ──────────────────────────────────────────────────────

class TestEmbeddingCacheHash:
    def test_hash_stable(self, registry):
        h1 = registry._compute_phrase_hash()
        h2 = registry._compute_phrase_hash()
        assert h1 == h2

    def test_hash_changes_on_phrase_addition(self, tmp_path):
        # cfg2 adds a new intent inside the intents: block
        cfg2_yaml = MINIMAL_YAML.replace(
            "matching:\n",
            "  new_intent:\n"
            "    phrases: [new phrase only in cfg2]\n"
            "    action: test_action\n"
            "matching:\n",
        )
        cfg1 = tmp_path / "cfg1.yaml"
        cfg1.write_text(MINIMAL_YAML)
        cfg2 = tmp_path / "cfg2.yaml"
        cfg2.write_text(cfg2_yaml)
        with patch.object(Registry, '_load_apps_from_desktop', return_value={}):
            r1 = Registry(str(cfg1))
            r2 = Registry(str(cfg2))
        assert r1._compute_phrase_hash() != r2._compute_phrase_hash()
