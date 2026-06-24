<!--
Copyright (c) 2026 raffishquartan. All rights reserved.
Licensed for personal use only.
-->

# last-screenshot Hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `UserPromptSubmit` hook so the token `>lss` in a prompt makes Claude pick up the user's most recent screenshot without typing its path.

**Architecture:** A new `cccs_hooks.last_screenshot` module (pure logic + a thin `main()`), dispatched by `ccst hooks run last-screenshot`, wrapped by a shell script in the config-sync repo, registered via `config/hooks-bundle.json`. The hook injects **text only** (the path + a "which case" note) as `UserPromptSubmit` additional context; the image enters context only if Claude then `Read`s the path.

**Tech Stack:** Python 3.12, stdlib only (`re`, `json`, `os`, `pathlib`, `time`); pytest; the existing `cccs_hooks` / `ccst` hook framework.

**Commit policy for this plan:** Per Chris's instruction, **do not commit anything**. Build and run tests in the working tree; leave all changes uncommitted for Chris to review and commit himself when he asks.

**Spec:** `docs/superpowers/specs/2026-06-16-last-screenshot-hook-design.md`

**Config (decided):** Screenshot directory comes from **env var `CCST_SCREENSHOT_DIR` only**. If `>lss` is used while it is unset, the hook surfaces a **visible** message to the user (stderr — which CC shows to the user) telling them to set it, in addition to the Claude-facing context note. The install step sets the env var in `settings.json`'s `env` block. Keeps committed code PII-free with no templating machinery.

---

## File Structure

- **Create** `src/cccs_hooks/last_screenshot.py` — all hook logic (pure functions + `main()`).
- **Create** `tests/test_last_screenshot.py` — unit + CLI tests.
- **Modify** `src/cc_session_tools/cli/ccst.py` — add `HOOK_VERBS` + `HOOK_DESCRIPTIONS` entries.
- **Create** `~/repos/claude-code-config-sync/hooks/user-prompt-submit/last-screenshot.sh` — shell wrapper.
- **Modify** `config/hooks-bundle.json` — add the `UserPromptSubmit` entry.

---

## Task 1: Core pure logic (token match + resolution + note)

**Files:**
- Create: `src/cccs_hooks/last_screenshot.py`
- Test: `tests/test_last_screenshot.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for cccs_hooks.last_screenshot."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from cccs_hooks.last_screenshot import (
    find_token, newest_screenshot, resolve_screenshot_dir, build_context,
)


@pytest.mark.parametrize("text", [
    ">lss", ">lss?", ">lss.", "(>lss is interesting)", "loss at >lss.",
    "summarise >lss please",
])
def test_token_matches(text: str) -> None:
    assert find_token(text) is True


@pytest.mark.parametrize("text", [
    "", ">lssfoo", "process>lssbar", "no token here", "lss", ">LSS",
])
def test_token_does_not_match(text: str) -> None:
    assert find_token(text) is False


def test_newest_screenshot_picks_highest_mtime(tmp_path: Path) -> None:
    old = tmp_path / "Screenshot old.png"
    new = tmp_path / "Screenshot new.png"
    old.write_bytes(b"x"); new.write_bytes(b"x")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    assert newest_screenshot(tmp_path) == new


def test_newest_screenshot_ignores_non_images(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_bytes(b"x")
    img = tmp_path / "shot.PNG"
    img.write_bytes(b"x")
    assert newest_screenshot(tmp_path) == img


def test_newest_screenshot_empty_dir_returns_none(tmp_path: Path) -> None:
    assert newest_screenshot(tmp_path) is None


def test_resolve_dir_uses_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_SCREENSHOT_DIR", str(tmp_path))
    assert resolve_screenshot_dir() == tmp_path


def test_resolve_dir_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("CCST_SCREENSHOT_DIR", raising=False)
    assert resolve_screenshot_dir() is None


def test_context_fresh_mentions_path_and_lss(tmp_path: Path) -> None:
    p = tmp_path / "shot.png"
    ctx = build_context(path=p, age_seconds=60, dir_configured=True)
    assert str(p) in ctx and ">lss" in ctx
    assert "older than" not in ctx


def test_context_stale_warns(tmp_path: Path) -> None:
    p = tmp_path / "shot.png"
    ctx = build_context(path=p, age_seconds=47 * 60, dir_configured=True)
    assert "older than" in ctx


def test_context_no_dir_configured() -> None:
    ctx = build_context(path=None, age_seconds=None, dir_configured=False)
    assert "CCST_SCREENSHOT_DIR" in ctx


def test_context_dir_but_no_image() -> None:
    ctx = build_context(path=None, age_seconds=None, dir_configured=True)
    assert "no screenshot" in ctx.lower()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd ~/repos/claude-code-session-tools && python -m pytest tests/test_last_screenshot.py -v`
Expected: FAIL — `ModuleNotFoundError: cccs_hooks.last_screenshot`.

- [ ] **Step 3: Implement the pure logic**

```python
"""UserPromptSubmit hook: resolve the user's latest screenshot for ">lss".

When a submitted prompt contains the token ">lss", inject the absolute path of
the newest screenshot plus a note telling Claude whether to read it. Text only;
the image enters context only if Claude then calls Read. Always exits 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_TOKEN = re.compile(r"(?<![A-Za-z0-9])>lss(?![A-Za-z0-9])")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_STALE_SECONDS = 10 * 60


def find_token(text: str) -> bool:
    """True if the prompt contains a standalone ">lss" token."""
    return _TOKEN.search(text) is not None


def resolve_screenshot_dir() -> Path | None:
    """The configured screenshot directory, or None if unset."""
    raw = os.environ.get("CCST_SCREENSHOT_DIR")
    return Path(raw) if raw else None


def newest_screenshot(directory: Path) -> Path | None:
    """The newest image file in ``directory`` by mtime, or None."""
    images = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    ]
    if not images:
        return None
    return max(images, key=lambda p: p.stat().st_mtime)


def _format_age(age_seconds: float) -> str:
    minutes = int(age_seconds // 60)
    if minutes < 1:
        return "less than a minute ago"
    return f"{minutes}m ago"


def build_context(
    *, path: Path | None, age_seconds: float | None, dir_configured: bool
) -> str:
    """The additional-context note injected for a ">lss" prompt."""
    if not dir_configured:
        return (
            '[last-screenshot] The message contains ">lss" but no screenshot '
            "directory is configured. Set the CCST_SCREENSHOT_DIR environment "
            "variable to the folder where screenshots are saved."
        )
    if path is None:
        return (
            '[last-screenshot] The message contains ">lss" but no screenshot '
            "was found in the configured directory."
        )
    note = (
        f'[last-screenshot] The user\'s message contains ">lss". If they are '
        f"asking you to look at their latest screenshot, it is at {path} "
        f"(taken {_format_age(age_seconds or 0)}). If they are only discussing "
        'the ">lss" feature itself, ignore this and do not read the file.'
    )
    if (age_seconds or 0) > _STALE_SECONDS:
        note += (
            f" Note: this screenshot is older than "
            f"{_STALE_SECONDS // 60} min - confirm it is the one they meant."
        )
    return note
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python -m pytest tests/test_last_screenshot.py -v`
Expected: PASS (all Task 1 tests).

---

## Task 2: `main()` entry point (stdin JSON -> additionalContext)

**Files:**
- Modify: `src/cccs_hooks/last_screenshot.py`
- Test: `tests/test_last_screenshot.py`

- [ ] **Step 1: Write failing CLI tests**

```python
import json, subprocess, sys


def _run(prompt: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "cccs_hooks.last_screenshot"],
        input=json.dumps({"prompt": prompt, "session_id": "t", "cwd": "/tmp"}),
        capture_output=True, text=True, env=full_env,
    )


def test_cli_silent_without_token(tmp_path) -> None:
    r = _run("just a normal prompt", {"CCST_SCREENSHOT_DIR": str(tmp_path)})
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_cli_emits_context_with_token(tmp_path) -> None:
    (tmp_path / "shot.png").write_bytes(b"x")
    r = _run("look at >lss", {"CCST_SCREENSHOT_DIR": str(tmp_path)})
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "shot.png" in ctx


def test_cli_exits_0_on_bad_json() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "cccs_hooks.last_screenshot"],
        input="not json", capture_output=True, text=True,
    )
    assert r.returncode == 0


def test_cli_unset_dir_is_visible_to_user(monkeypatch) -> None:
    env = {k: v for k, v in os.environ.items() if k != "CCST_SCREENSHOT_DIR"}
    r = subprocess.run(
        [sys.executable, "-m", "cccs_hooks.last_screenshot"],
        input=json.dumps({"prompt": "look at >lss"}),
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0
    assert "CCST_SCREENSHOT_DIR" in r.stderr
```

- [ ] **Step 2: Run, verify fail** — `python -m pytest tests/test_last_screenshot.py -k cli -v` -> FAIL (no `main`).

- [ ] **Step 3: Implement `main()`**

```python
def _emit(context: str) -> None:
    json.dump(
        {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }},
        sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return 0
    prompt = str(data.get("prompt", ""))
    if not prompt or not find_token(prompt):
        return 0

    directory = resolve_screenshot_dir()
    if directory is None or not directory.is_dir():
        _emit(build_context(path=None, age_seconds=None, dir_configured=False))
        return 0
    shot = newest_screenshot(directory)
    if shot is None:
        _emit(build_context(path=None, age_seconds=None, dir_configured=True))
        return 0
    age = time.time() - shot.stat().st_mtime
    _emit(build_context(path=shot, age_seconds=age, dir_configured=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run, verify pass** — `python -m pytest tests/test_last_screenshot.py -v` -> PASS.

---

## Task 3: Register the hook in the `ccst` dispatcher

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py:37` (HOOK_VERBS) and `:47` (HOOK_DESCRIPTIONS)

- [ ] **Step 1:** Add to `HOOK_VERBS`:

```python
    "last-screenshot": "cccs_hooks.last_screenshot",
```

- [ ] **Step 2:** Add to `HOOK_DESCRIPTIONS`:

```python
    "last-screenshot": "Resolves the newest screenshot for the >lss token and injects its path",
```

- [ ] **Step 3: Verify dispatch + no regressions**

Run: `echo '{"prompt":"x"}' | ccst hooks run last-screenshot` -> exits 0, no output.
Run: `python -m pytest tests/test_ccst_hook_dispatcher.py tests/test_ccst_doctor.py tests/test_ccst_cli.py -v`
Expected: PASS. If `test_ccst_doctor.py` enumerates bundle hooks, it stays green until Task 4 adds the bundle entry — if it fails after Task 4, that test is asserting bundle/settings parity (resolve in Task 4).

---

## Task 4: Shell wrapper + bundle entry

**Files:**
- Create: `~/repos/claude-code-config-sync/hooks/user-prompt-submit/last-screenshot.sh`
- Modify: `config/hooks-bundle.json` (UserPromptSubmit block)

- [ ] **Step 1:** Create the wrapper (mode `0700`, mirroring `prompt-guard.sh`):

```bash
#!/usr/bin/env bash
# Copyright (c) 2026 raffishquartan. All rights reserved.
# Licensed for personal use only.

# UserPromptSubmit hook: resolve the latest screenshot for the >lss token.
INPUT=$(cat)
exec ccst hooks run last-screenshot <<< "$INPUT"
```

Then: `chmod 700 ~/repos/claude-code-config-sync/hooks/user-prompt-submit/last-screenshot.sh`

- [ ] **Step 2:** Add to the `UserPromptSubmit` array in `config/hooks-bundle.json`:

```json
{
  "type": "command",
  "command": "ccst hooks run last-screenshot",
  "timeout": 5,
  "statusMessage": "Resolving latest screenshot for >lss..."
}
```

- [ ] **Step 3: Verify bundle parses + tests green**

Run: `python -c "import json; json.load(open('config/hooks-bundle.json'))"` -> no error.
Run: `python -m pytest tests/ -q`
Expected: PASS. Fix any doctor/bundle-parity test that now expects `last-screenshot` registered.

---

## Task 5: Install, configure env var, manual smoke test

- [ ] **Step 1:** Register into live settings: `ccst hooks install --hook last-screenshot` (review the diff first).
- [ ] **Step 2:** Set the screenshot dir. Add to `~/.claude/settings.json` `env` block:
  `"CCST_SCREENSHOT_DIR": "<the user's screenshots folder>"` (Chris supplies the value; do not hardcode it in any committed file).
- [ ] **Step 3: Manual smoke test** in a fresh CC session:
  - Type a prompt containing `>lss` -> confirm Claude is told the newest screenshot's path and offers to read it.
  - Type a prompt that mentions `>lss` meta-style ("what's the >lss hook?") -> confirm no image is read.
  - Temporarily unset `CCST_SCREENSHOT_DIR` -> confirm the "not configured" note.
- [ ] **Step 4:** Run the full suite once more: `python -m pytest tests/ -q`.

---

## Done criteria

- `python -m pytest tests/ -q` is fully green.
- `>lss` in a real prompt surfaces the newest screenshot's path; meta-talk does not.
- No personal path committed; the concrete directory lives only in local settings.
- All changes left **uncommitted** for Chris to review.
