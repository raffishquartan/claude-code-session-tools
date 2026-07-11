"""Tests for `ccst hooks run <name>` dispatcher subcommand."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.cli.ccst import HOOK_VERBS


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
    assert "no-such-hook" in result.stderr or "invalid choice" in result.stderr


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
    }
    assert set(HOOK_VERBS) == expected
