from __future__ import annotations

from cc_session_tools.lib import debug


def test_is_debug_false_by_default(monkeypatch):
    monkeypatch.delenv("CCX_DEBUG", raising=False)
    assert debug.is_debug() is False


def test_is_debug_true_when_set(monkeypatch):
    monkeypatch.setenv("CCX_DEBUG", "1")
    assert debug.is_debug() is True


def test_is_debug_false_for_zero(monkeypatch):
    monkeypatch.setenv("CCX_DEBUG", "0")
    assert debug.is_debug() is False


def test_debug_prints_to_stderr_when_enabled(monkeypatch, capsys):
    monkeypatch.setenv("CCX_DEBUG", "1")
    debug.debug("roots:", ["/foo"])
    err = capsys.readouterr().err
    assert "[CCX_DEBUG] roots: ['/foo']" in err


def test_debug_silent_when_disabled(monkeypatch, capsys):
    monkeypatch.delenv("CCX_DEBUG", raising=False)
    debug.debug("should not appear")
    assert capsys.readouterr().err == ""
