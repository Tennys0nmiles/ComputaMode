"""Tests for src/voice/executor.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.voice.executor import Executor, BUILT_IN
from src.voice.interpreter import MatchResult


def make_result(action, tier="1", needs_confirm=False, intent_name="test"):
    return MatchResult(
        intent_name=intent_name,
        action=action,
        confidence=1.0,
        tier=tier,
        args={},
        needs_confirm=needs_confirm,
    )


def make_executor(dry_run=False):
    mouse = MagicMock()
    state = {"paused": False}
    return Executor(mouse, state, tts=None, dry_run=dry_run), mouse, state


# ── Shell safety ──────────────────────────────────────────────────────────────

class TestShlexSafety:
    def test_normal_split_uses_list(self):
        executor, _, _ = make_executor()
        with patch('subprocess.Popen') as mock_popen:
            executor._execute_shell("echo hello world")
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert args == ["echo", "hello", "world"]

    def test_shell_false(self):
        executor, _, _ = make_executor()
        with patch('subprocess.Popen') as mock_popen:
            executor._execute_shell("echo test")
            _, kwargs = mock_popen.call_args
            assert kwargs.get("shell") is False

    def test_malformed_returns_false_not_raise(self):
        executor, _, _ = make_executor()
        result = executor._execute_shell("echo 'unclosed quote")
        assert result is False

    def test_empty_returns_false(self):
        executor, _, _ = make_executor()
        result = executor._execute_shell("")
        assert result is False


# ── Builtins ──────────────────────────────────────────────────────────────────

class TestBuiltins:
    @pytest.mark.parametrize("action", [
        "scroll_up", "scroll_down", "left_click", "right_click",
        "pause_gestures", "resume_gestures",
    ])
    def test_builtin_handled(self, action):
        executor, mouse, state = make_executor()
        result = executor._execute_builtin(action)
        assert result is True

    def test_pause_gestures_mutates_state(self):
        executor, mouse, state = make_executor()
        executor._execute_builtin("pause_gestures")
        assert state["paused"] is True

    def test_resume_gestures_mutates_state(self):
        executor, mouse, state = make_executor()
        state["paused"] = True
        executor._execute_builtin("resume_gestures")
        assert state["paused"] is False

    def test_scroll_up_calls_mouse(self):
        executor, mouse, _ = make_executor()
        executor._execute_builtin("scroll_up")
        mouse.scroll.assert_called_once_with(3)

    def test_scroll_down_calls_mouse(self):
        executor, mouse, _ = make_executor()
        executor._execute_builtin("scroll_down")
        mouse.scroll.assert_called_once_with(-3)


# ── Dry run ───────────────────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_prints_no_popen(self, capsys):
        executor, _, _ = make_executor(dry_run=True)
        with patch('subprocess.Popen') as mock_popen:
            result = executor.execute(make_result("echo hello"))
            mock_popen.assert_not_called()
        assert result is True
        captured = capsys.readouterr()
        assert "echo hello" in captured.out

    def test_dry_run_true_calls_popen_with_shell_false(self):
        executor, _, _ = make_executor(dry_run=False)
        with patch('subprocess.Popen') as mock_popen:
            executor.execute(make_result("echo hello"))
            mock_popen.assert_called_once()
            _, kwargs = mock_popen.call_args
            assert kwargs.get("shell") is False

    def test_empty_action_returns_false(self):
        executor, _, _ = make_executor()
        result = executor.execute(make_result(""))
        assert result is False
