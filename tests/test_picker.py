from __future__ import annotations

import pytest
from cc_session_tools.lib.picker import pick_from_list


def _pick(labels, user_input, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: user_input)
    return pick_from_list(labels)


def test_pick_first_of_two(monkeypatch):
    assert _pick(["a", "b"], "1", monkeypatch) == 0


def test_pick_second_of_two(monkeypatch):
    assert _pick(["a", "b"], "2", monkeypatch) == 1


def test_pick_ninth(monkeypatch):
    labels = [str(i) for i in range(9)]
    assert _pick(labels, "9", monkeypatch) == 8


def test_pick_tenth_via_zero(monkeypatch):
    labels = [str(i) for i in range(10)]
    assert _pick(labels, "0", monkeypatch) == 9


def test_cancel_with_q(monkeypatch):
    assert _pick(["a", "b"], "q", monkeypatch) is None


def test_cancel_with_empty(monkeypatch):
    assert _pick(["a", "b"], "", monkeypatch) is None


def test_out_of_range_returns_none(monkeypatch):
    # 3 items, digit 9 is out of range
    assert _pick(["a", "b", "c"], "9", monkeypatch) is None


def test_eof_returns_none(monkeypatch):
    def raise_eof(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", raise_eof)
    assert pick_from_list(["a", "b"]) is None


def test_keyboard_interrupt_returns_none(monkeypatch):
    def raise_ki(_):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", raise_ki)
    assert pick_from_list(["a", "b"]) is None


def test_display_shows_1_to_9_numbering(monkeypatch, capsys):
    labels = ["alpha", "beta", "gamma"]
    monkeypatch.setattr("builtins.input", lambda _: "q")
    pick_from_list(labels)
    out = capsys.readouterr().out
    assert "1) alpha" in out
    assert "2) beta" in out
    assert "3) gamma" in out


def test_display_shows_0_for_tenth(monkeypatch, capsys):
    labels = [str(i) for i in range(10)]
    monkeypatch.setattr("builtins.input", lambda _: "q")
    pick_from_list(labels)
    out = capsys.readouterr().out
    assert "0)" in out
