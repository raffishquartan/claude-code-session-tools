"""Tests for the marker-allow PreToolUse hook.

Covers the pure matcher (`match_marker_touch`) and the stdin/stdout `main`
contract: a bare `touch <markers-dir>/<name>` is auto-approved; everything else
- compound commands, expansions, redirections, extra args, out-of-dir paths,
non-Bash tools - is left untouched (no output, exit 0).
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cccs_hooks.marker_allow import main, match_marker_touch


# ---------- match_marker_touch (pure) ----------


@pytest.fixture
def markers(tmp_path: Path) -> Path:
    d = tmp_path / ".claude" / "hooks" / "markers"
    d.mkdir(parents=True)
    return d


def test_match_absolute_path(markers: Path) -> None:
    cmd = f"touch {markers}/tesco_shop_active"
    assert match_marker_touch(cmd, markers) == "tesco_shop_active"


def test_match_strips_surrounding_whitespace(markers: Path) -> None:
    cmd = f"   touch {markers}/foo   "
    assert match_marker_touch(cmd, markers) == "foo"


def test_match_tilde_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # With HOME=tmp_path, ~ expands to tmp_path and markers is its child.
    monkeypatch.setenv("HOME", str(tmp_path))
    markers = tmp_path / ".claude" / "hooks" / "markers"
    cmd = "touch ~/.claude/hooks/markers/telegram_notify"
    assert match_marker_touch(cmd, markers) == "telegram_notify"


@pytest.mark.parametrize("name", ["a", "a.b", "a_b-c", "marker.lock", "X9"])
def test_match_safe_names(markers: Path, name: str) -> None:
    assert match_marker_touch(f"touch {markers}/{name}", markers) == name


@pytest.mark.parametrize(
    "cmd_tail",
    [
        "&& rm -rf /tmp/x",  # command chaining
        "&& echo done",  # the form the skill previously used
        "; echo hi",  # sequencing
        "| cat",  # pipe
        "> /tmp/out",  # redirection
        "$(whoami)",  # command substitution
        "`whoami`",  # backtick substitution
    ],
)
def test_no_match_shell_metacharacters(markers: Path, cmd_tail: str) -> None:
    cmd = f"touch {markers}/foo {cmd_tail}"
    assert match_marker_touch(cmd, markers) is None


def test_no_match_glob_in_name(markers: Path) -> None:
    assert match_marker_touch(f"touch {markers}/*", markers) is None


def test_no_match_extra_argument(markers: Path) -> None:
    cmd = f"touch {markers}/foo {markers}/bar"
    assert match_marker_touch(cmd, markers) is None


def test_no_match_flag(markers: Path) -> None:
    cmd = f"touch -c {markers}/foo"
    assert match_marker_touch(cmd, markers) is None


def test_no_match_not_touch(markers: Path) -> None:
    assert match_marker_touch(f"rm {markers}/foo", markers) is None


def test_no_match_path_traversal_escapes_dir(markers: Path) -> None:
    # Normalises to the parent of markers -> rejected.
    cmd = f"touch {markers}/../evil"
    assert match_marker_touch(cmd, markers) is None


def test_no_match_sibling_directory(markers: Path) -> None:
    sibling = markers.parent / "other" / "foo"
    assert match_marker_touch(f"touch {sibling}", markers) is None


def test_no_match_nested_under_markers(markers: Path) -> None:
    # A grandchild is not a direct child -> rejected.
    cmd = f"touch {markers}/sub/foo"
    assert match_marker_touch(cmd, markers) is None


def test_no_match_markers_dir_itself(markers: Path) -> None:
    assert match_marker_touch(f"touch {markers}", markers) is None


def test_no_match_bare_relative_name(markers: Path) -> None:
    assert match_marker_touch("touch foo", markers) is None


def test_no_match_empty(markers: Path) -> None:
    assert match_marker_touch("", markers) is None
    assert match_marker_touch("   ", markers) is None


# ---------- main (stdin -> stdout) ----------


def _run_main(
    monkeypatch: pytest.MonkeyPatch, home: Path, payload: object
) -> str:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CCCS_MARKERS_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = main()
    assert rc == 0
    return out.getvalue()


def test_main_allows_marker_touch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".cache" / "claude" / "markers").mkdir(parents=True)
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "touch ~/.cache/claude/markers/tesco_shop_active"},
    }
    out = _run_main(monkeypatch, tmp_path, payload)
    decision = json.loads(out)
    hso = decision["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "allow"
    assert "tesco_shop_active" in hso["permissionDecisionReason"]


def test_main_silent_on_compound_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".cache" / "claude" / "markers").mkdir(parents=True)
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "touch ~/.cache/claude/markers/x && rm -rf ~"
        },
    }
    assert _run_main(monkeypatch, tmp_path, payload) == ""


def test_main_silent_on_non_bash_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {
        "tool_name": "Write",
        "tool_input": {"command": "touch ~/.cache/claude/markers/x"},
    }
    assert _run_main(monkeypatch, tmp_path, payload) == ""


def test_main_silent_on_unrelated_bash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
    assert _run_main(monkeypatch, tmp_path, payload) == ""


def test_main_handles_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert main() == 0
    assert out.getvalue() == ""


def test_main_handles_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert main() == 0
    assert out.getvalue() == ""
