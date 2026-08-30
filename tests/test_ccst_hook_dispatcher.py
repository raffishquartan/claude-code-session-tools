"""Tests for `ccst hooks run <name>` dispatcher subcommand."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.hook_registry import HOOK_VERBS


def _run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        input=stdin,
        cwd=str(Path(__file__).parent.parent),
    )


def test_hooks_run_unknown_hook_name_rejected() -> None:
    result = _run("hooks", "run", "no-such-hook")
    assert result.returncode != 0
    assert "no-such-hook" in result.stderr


@pytest.mark.parametrize("removed_hook", ["prompt-guard", "edit-write-audit", "session-end"])
def test_unknown_hook_never_exits_2(removed_hook: str) -> None:
    """Exit 2 is Claude Code's *blocking* code; every other non-zero value is
    a non-blocking error. A settings.json entry naming a removed hook is a
    stale-config problem, so it must warn, not block.

    These three names are the ones that wedged a real install: argparse's
    `choices=` rejected them with its own exit code (2), which made a stale
    UserPromptSubmit entry swallow every prompt and a stale Stop entry stop
    the session from ever ending.
    """
    result = _run("hooks", "run", removed_hook, stdin="{}")
    assert result.returncode == 1
    assert result.returncode != 2


def test_unknown_hook_message_says_how_to_remove_the_stale_entry() -> None:
    result = _run("hooks", "run", "prompt-guard", stdin="{}")
    assert "ccst hooks uninstall --hook prompt-guard --apply" in result.stderr


@pytest.mark.parametrize("verb", sorted(HOOK_VERBS))
def test_every_known_verb_points_to_importable_module_with_main(verb: str) -> None:
    module = importlib.import_module(HOOK_VERBS[verb])
    assert callable(module.main)


def test_hook_verbs_covers_every_shim_module() -> None:
    expected = {
        "bash-hard-deny",
        "bash-security-review",
        "marker-allow",
        "confirm-8digit",
        "after-response",
        "worklog-guard",
        "session-tag",
        "last-screenshot",
        "messaging-deliver",
        "catchup",
        "pending-migration",
        "context-window-warning",
        "pending-rename",
        "pdata-sync",
    }
    assert set(HOOK_VERBS) == expected
