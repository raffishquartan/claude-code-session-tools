"""Record layout of the hooks install output (`_format_hooks_install_records`)."""
from __future__ import annotations

import os
import shutil

import pytest

from cc_session_tools.cli.ccst import _format_hooks_install_records

Inv = list[tuple[str, str, str | None, str]]


def _width(monkeypatch: pytest.MonkeyPatch, columns: int) -> None:
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(80, 24): os.terminal_size((columns, 24))
    )


def _inv() -> Inv:
    return [
        ("catchup", "SessionStart", None, "ccst hooks run catchup"),
        ("catchup", "UserPromptSubmit", None, "ccst hooks run catchup"),
        ("confirm-8digit", "PreToolUse", None, "ccst hooks run confirm-8digit"),
    ]


def test_one_record_per_hook_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 80)
    out = _format_hooks_install_records(_inv(), set())
    headers = [ln for ln in out.splitlines() if ln and not ln.startswith(" ")]
    assert [h.split()[0] for h in headers] == ["catchup", "confirm-8digit"]


def test_record_shape_and_blank_line_separation(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 80)
    out = _format_hooks_install_records(_inv(), set())
    assert not out.endswith("\n")
    first, second = out.split("\n\n")
    lines = first.splitlines()
    assert lines[0].startswith("catchup ")
    assert lines[1] == "    Events: SessionStart, UserPromptSubmit"
    assert all(ln.startswith("    ") for ln in lines[1:])
    assert second.splitlines()[0].startswith("confirm-8digit ")
    assert "Description" not in out and "---" not in out


def test_description_printed_once_for_multi_event_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 200)
    out = _format_hooks_install_records(_inv(), set())
    assert out.count("Reconciles+launches") == 1


def test_status_all_new_and_all_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 80)
    inv = _inv()
    added = {(e, m, c) for _, e, m, c in inv}
    new = _format_hooks_install_records(inv, added)
    assert new.splitlines()[0].split()[1] == "install"
    assert "(new)" not in new
    old = _format_hooks_install_records(inv, set())
    assert old.splitlines()[0].split()[1] == "already-installed"


def test_partly_installed_multi_event_hook_marks_new_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _width(monkeypatch, 80)
    added: set[tuple[str, str | None, str]] = {("UserPromptSubmit", None, "ccst hooks run catchup")}
    out = _format_hooks_install_records(_inv(), added)
    lines = out.splitlines()
    assert lines[0].split()[1] == "install"
    assert lines[1] == "    Events: SessionStart, UserPromptSubmit (new)"


def test_no_events_or_description_line_exceeds_width(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 80)
    from cc_session_tools.lib.hook_registry import HOOK_DESCRIPTIONS

    inv: Inv = [(n, "SessionStart", None, f"ccst hooks run {n}") for n in HOOK_DESCRIPTIONS]
    out = _format_hooks_install_records(inv, set())
    for ln in out.splitlines():
        if ln.startswith("    "):
            assert len(ln) <= 80, ln


def test_no_description_prints_header_and_events_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 80)
    cmd = "/opt/tools/run-thing.sh --flag value"
    out = _format_hooks_install_records([(cmd, "Stop", None, cmd)], set())
    lines = out.splitlines()
    assert lines[0].startswith(cmd)
    assert lines[1] == "    Events: Stop"
    assert len(lines) == 2


def test_matcher_in_event_label(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 80)
    inv: Inv = [("marker-allow", "PreToolUse", "Bash", "ccst hooks run marker-allow")]
    assert "Events: PreToolUse[Bash]" in _format_hooks_install_records(inv, set())


def test_empty_inventory_is_empty_string() -> None:
    assert _format_hooks_install_records([], set()) == ""
