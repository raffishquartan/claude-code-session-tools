# Plan 2: Foreign-session detection (`ccd`/`ccr` + origin-marker hook) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Read
> `docs/superpowers/plans/2026-08-02-multi-laptop-sync-00-overview.md` first for binding
> cross-plan decisions. This plan is independent of Plan 1 and Plan 3 — no shared files.

> **Revision note:** this plan originally had `ccl`/`ccs` tag every foreign session in its
> listings (per-session filesystem check). That design was rejected during review for two
> reasons, both confirmed against the live codebase: (1) it reintroduced a per-session
> filesystem stat into `ccs`'s list-mode print loops, breaking a pinned performance-regression
> test (`test_ccs_sentinel_sort.py::TestSessionEnumerationScaling`) that exists specifically
> because an earlier project phase eliminated per-session stats from `ccl`/`ccs`/`ccr` for
> scaling reasons; (2) more fundamentally, `ccs --global`'s enumeration is 100% `sessions.db`-
> driven, and `sessions.db` is machine-local — a session folder synced in from the other laptop
> but never opened locally has **no row at all**, so it likely wouldn't even appear in a `ccl`
> listing regardless of any per-session tagging logic. Chris's replacement design (this
> revision): don't touch `ccs`/`ccl` at all. Instead, (a) a SessionStart hook stamps every
> session with its originating machine as a plain, human-readable sibling file — pure
> provenance, no listing logic — and (b) `ccd`/`ccr` do one cheap, targeted lookup (never a scan)
> at the exact point they already need to resolve one specific session by name, using
> `sessions.db` row-existence as the "is this known locally" signal instead of any on-disk
> marker/JSONL fallback.

**Goal:** every `cc-sessions/<tag>/` folder gets stamped, once, with which laptop created it — a
plain file a human or Claude can read directly (spec's revised intent). `ccd` and `ccr` each
raise a clear flag when they encounter a named session that exists on disk but has no local
`sessions.db` row (the one signal that reliably means "not touched on this machine before" without
scanning anything). `ccr` refuses to resume such a session unless `--force` is passed (spec §2
goal 3).

**Architecture:** `lib/session_origin.py` is a tiny read/write helper for one plain-text marker
file. `cccs_hooks/session_origin_marker.py` is a new SessionStart hook — the *only* place the
marker is ever written, exactly once, idempotently, for both new (`ccd`) and resumed (`ccr`)
session starts (both already set the `CLD_SESSION_DIR` env var the hook reads). `ccd.py` and
`ccr.py` each gain a one-query `sessions.db` lookup at the single point where they already know
the exact basename in question — no directory walk, no per-listing cost.

**Tech Stack:** Python 3.11+, stdlib `argparse`/`os`/`pathlib`, `pytest`.

---

## File Structure

- Create: `src/cc_session_tools/lib/machine_identity.py`
- Create: `tests/test_machine_identity.py`
- Create: `src/cc_session_tools/lib/session_origin.py`
- Create: `tests/test_session_origin.py`
- Create: `src/cccs_hooks/session_origin_marker.py` — new SessionStart hook
- Create: `tests/test_session_origin_marker.py`
- Modify: `src/cc_session_tools/cli/ccst.py` — `HOOK_VERBS`/`HOOK_DESCRIPTIONS` (`ccst.py:79-106`)
- Modify: `config/hooks-bundle.json` — `SessionStart` array (`hooks-bundle.json:3-32`)
- Modify: `src/cc_session_tools/cli/ccd.py:175-185` — enrich the existing duplicate-session
  refusal message
- Modify: `tests/test_cli_ccd.py` — one new test
- Modify: `src/cc_session_tools/lib/sessions.py` — new `find_foreign_on_disk_session()`, placed
  beside `find_matching_sessions`/`find_orphan_transcripts` (`sessions.py:57-129`)
- Modify: `tests/test_sessions.py` — new tests for `find_foreign_on_disk_session`
- Modify: `src/cc_session_tools/cli/ccr.py` — `_build_parser` (`ccr.py:64-81`), the `if not
  matches:` branch (`ccr.py:118-120`)
- Modify: `tests/test_cli_ccr.py` — new tests

---

### Task 1: `lib/machine_identity.py`

**Files:**
- Create: `src/cc_session_tools/lib/machine_identity.py`
- Test: `tests/test_machine_identity.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_machine_identity.py
from __future__ import annotations

import pytest

from cc_session_tools.lib import machine_identity


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    monkeypatch.delenv(machine_identity.MACHINE_NAME_ENV, raising=False)


def test_env_override_wins_even_with_stored_file():
    machine_identity.machine_name_path().parent.mkdir(parents=True, exist_ok=True)
    machine_identity.machine_name_path().write_text("stored-name\n")
    import os
    os.environ[machine_identity.MACHINE_NAME_ENV] = "env-name"
    try:
        assert machine_identity.get_machine_name() == "env-name"
    finally:
        del os.environ[machine_identity.MACHINE_NAME_ENV]


def test_returns_stored_name_without_prompting(monkeypatch):
    machine_identity.machine_name_path().parent.mkdir(parents=True, exist_ok=True)
    machine_identity.machine_name_path().write_text("stored-name\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def fail_input(prompt):
        raise AssertionError("should not prompt when a name is already stored")

    monkeypatch.setattr("builtins.input", fail_input)
    assert machine_identity.get_machine_name() == "stored-name"


def test_first_run_noninteractive_stores_detected_hostname_silently(monkeypatch):
    monkeypatch.setattr(machine_identity, "detected_hostname", lambda: "detected-host")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    name = machine_identity.get_machine_name()
    assert name == "detected-host"
    assert machine_identity.machine_name_path().read_text().strip() == "detected-host"


def test_first_run_interactive_accepts_default_on_enter(monkeypatch):
    monkeypatch.setattr(machine_identity, "detected_hostname", lambda: "detected-host")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert machine_identity.get_machine_name() == "detected-host"


def test_first_run_interactive_accepts_override(monkeypatch):
    monkeypatch.setattr(machine_identity, "detected_hostname", lambda: "detected-host")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "my-custom-name")
    name = machine_identity.get_machine_name()
    assert name == "my-custom-name"
    assert machine_identity.machine_name_path().read_text().strip() == "my-custom-name"


def test_interactive_arg_false_skips_prompt_even_on_a_tty(monkeypatch):
    monkeypatch.setattr(machine_identity, "detected_hostname", lambda: "detected-host")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def fail_input(prompt):
        raise AssertionError("should not prompt when interactive=False")

    monkeypatch.setattr("builtins.input", fail_input)
    assert machine_identity.get_machine_name(interactive=False) == "detected-host"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_machine_identity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.machine_identity'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/machine_identity.py
"""Resolve/prompt/store this laptop's machine name (spec §4.2). The
*only* caller that ever passes interactive=True is a future direct CLI
invocation, if one is ever added — the session-origin SessionStart hook
(cccs_hooks/session_origin_marker.py) always passes interactive=False,
since a hook must never block session startup on a prompt."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

from cc_session_tools.lib import paths

MACHINE_NAME_ENV = "CCST_MACHINE_NAME"
_MACHINE_NAME_FILENAME = "machine-name"


def machine_name_path() -> Path:
    return paths.data_home() / _MACHINE_NAME_FILENAME


def detected_hostname() -> str:
    return socket.gethostname()


def get_machine_name(*, interactive: bool = True) -> str:
    """Resolve this machine's name: env override, else the stored file, else
    detect the hostname and store it — prompting to confirm/override on
    first run when `interactive` and stdin is a TTY (spec §4.2). A
    non-interactive first run stores the detected hostname without
    prompting rather than blocking."""
    override = os.environ.get(MACHINE_NAME_ENV)
    if override:
        return override

    stored_path = machine_name_path()
    if stored_path.is_file():
        stored = stored_path.read_text().strip()
        if stored:
            return stored

    detected = detected_hostname()
    if interactive and sys.stdin.isatty():
        try:
            raw = input(
                f"First run on this laptop — use '{detected}' as its machine name? "
                "[Y/n, or type a different name]: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        name = detected if not raw or raw.lower() == "y" else raw
    else:
        name = detected

    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_text(name + "\n")
    return name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_machine_identity.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/machine_identity.py tests/test_machine_identity.py
git commit -m "feat: add machine-identity resolution for multi-laptop setups"
```

---

### Task 2: `lib/session_origin.py` — plain-text marker read/write

**Files:**
- Create: `src/cc_session_tools/lib/session_origin.py`
- Test: `tests/test_session_origin.py`

Deliberately a plain-text file, not JSON — Chris's ask was specifically that "a human or Claude
can look at this file... to see where the session came from," which favours something readable
with `cat`/a text editor over something requiring JSON parsing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_origin.py
from __future__ import annotations

from cc_session_tools.lib import session_origin


def test_write_then_read_round_trips(tmp_path):
    session_origin.write_origin_marker(tmp_path, "laptop-a")
    assert session_origin.read_origin_marker(tmp_path) == "laptop-a"


def test_read_returns_none_when_marker_absent(tmp_path):
    assert session_origin.read_origin_marker(tmp_path) is None


def test_read_returns_none_for_empty_marker(tmp_path):
    (tmp_path / session_origin.ORIGIN_MARKER_FILENAME).write_text("")
    assert session_origin.read_origin_marker(tmp_path) is None


def test_write_strips_whitespace(tmp_path):
    session_origin.write_origin_marker(tmp_path, "  laptop-a  \n")
    assert session_origin.read_origin_marker(tmp_path) == "laptop-a"


def test_marker_is_a_sibling_of_working_and_out(tmp_path):
    (tmp_path / "working").mkdir()
    (tmp_path / "out").mkdir()
    session_origin.write_origin_marker(tmp_path, "laptop-a")
    marker = tmp_path / session_origin.ORIGIN_MARKER_FILENAME
    assert marker.parent == tmp_path
    assert marker.is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session_origin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.session_origin'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/session_origin.py
"""Per-session origin marker: records which machine created a
cc-sessions/<tag>/ directory. Written once by the session-origin
SessionStart hook (cccs_hooks/session_origin_marker.py), as a plain-text
sibling of that directory's working/ and out/ subfolders — readable
directly by a human or by Claude with no parsing required."""
from __future__ import annotations

from pathlib import Path

ORIGIN_MARKER_FILENAME = "origin-machine.txt"


def write_origin_marker(session_dir: Path, machine: str) -> None:
    (session_dir / ORIGIN_MARKER_FILENAME).write_text(machine.strip() + "\n")


def read_origin_marker(session_dir: Path) -> str | None:
    """The recorded machine name, or None if the marker is absent or empty."""
    marker = session_dir / ORIGIN_MARKER_FILENAME
    if not marker.is_file():
        return None
    content = marker.read_text().strip()
    return content or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_origin.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/session_origin.py tests/test_session_origin.py
git commit -m "feat: add plain-text session-origin marker read/write"
```

---

### Task 3: `session-origin` SessionStart hook

**Files:**
- Create: `src/cccs_hooks/session_origin_marker.py`
- Test: `tests/test_session_origin_marker.py`
- Modify: `src/cc_session_tools/cli/ccst.py:79-106` (`HOOK_VERBS`, `HOOK_DESCRIPTIONS`)
- Modify: `config/hooks-bundle.json:3-32` (`SessionStart` array)

This hook fires on every session start — both `ccd` (new) and `ccr` (resume) set
`CLD_SESSION_DIR` before exec'ing `claude` (`ccd.py:201`, `ccr.py:197`), and both are inherited by
the hook's own environment (confirmed: `claude` is exec'd via `os.execvpe`, replacing the ccd/ccr
process, and every hook is a child process of that `claude` process — the existing
`cccs_hooks/session_tag.py` hook already relies on exactly this). The hook is idempotent — it
only ever writes once, on whichever session start is first to see no marker yet — so it always
reflects the *originating* machine even after many later resumes on either laptop. Matches every
sibling `SessionStart` hook's contract: never blocks (`main()` always returns `0`); a write
failure degrades to a `stderr` line, never a nonzero exit.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_origin_marker.py
"""Tests for cccs_hooks.session_origin_marker — SessionStart hook that
stamps cc-sessions/<tag>/ with the machine that created it."""
from __future__ import annotations

import pytest

from cccs_hooks import session_origin_marker
from cc_session_tools.lib import session_origin


@pytest.fixture(autouse=True)
def _clear_cld_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    # Isolate machine_identity's fallback path (paths.data_home()/"machine-name") so a test
    # that doesn't set CCST_MACHINE_NAME never touches this machine's real stored file.
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))


def test_no_session_dir_env_is_a_noop(monkeypatch):
    rc = session_origin_marker.main()
    assert rc == 0


def test_nonexistent_session_dir_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("CLD_SESSION_DIR", str(tmp_path / "does-not-exist"))
    rc = session_origin_marker.main()
    assert rc == 0


def test_writes_marker_with_machine_name(tmp_path, monkeypatch):
    session_dir = tmp_path / "cc-sessions" / "20260101-mytag"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("CLD_SESSION_DIR", str(session_dir))
    monkeypatch.setenv("CCST_MACHINE_NAME", "test-laptop")

    rc = session_origin_marker.main()

    assert rc == 0
    assert session_origin.read_origin_marker(session_dir) == "test-laptop"


def test_does_not_overwrite_an_existing_marker(tmp_path, monkeypatch):
    session_dir = tmp_path / "cc-sessions" / "20260101-mytag"
    session_dir.mkdir(parents=True)
    session_origin.write_origin_marker(session_dir, "original-laptop")
    monkeypatch.setenv("CLD_SESSION_DIR", str(session_dir))
    monkeypatch.setenv("CCST_MACHINE_NAME", "different-laptop")

    rc = session_origin_marker.main()

    assert rc == 0
    assert session_origin.read_origin_marker(session_dir) == "original-laptop"


def test_never_prompts_for_machine_name(tmp_path, monkeypatch):
    session_dir = tmp_path / "cc-sessions" / "20260101-mytag"
    session_dir.mkdir(parents=True)
    monkeypatch.setenv("CLD_SESSION_DIR", str(session_dir))
    monkeypatch.delenv("CCST_MACHINE_NAME", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def fail_input(prompt):
        raise AssertionError("a SessionStart hook must never block on a prompt")

    monkeypatch.setattr("builtins.input", fail_input)

    rc = session_origin_marker.main()
    assert rc == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session_origin_marker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cccs_hooks.session_origin_marker'`

- [ ] **Step 3: Write the implementation**

```python
# src/cccs_hooks/session_origin_marker.py
"""SessionStart hook: stamp cc-sessions/<tag>/ with the machine that created
it (multi-laptop sync spec §4.2/§4.3-revised). Write-once and idempotent —
whichever session start first sees no marker writes it, so it always
reflects the originating machine, not whichever machine most recently
resumed the session. Never blocks: any failure degrades to a stderr line,
exit is always 0, matching every sibling SessionStart hook in this package
(session_tag.py, catchup.py, pending_migration.py, messaging_deliver.py)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    session_dir_raw = os.environ.get("CLD_SESSION_DIR")
    if not session_dir_raw:
        return 0
    session_dir = Path(session_dir_raw)
    if not session_dir.is_dir():
        return 0

    from cc_session_tools.lib import machine_identity, session_origin

    if session_origin.read_origin_marker(session_dir) is not None:
        return 0  # already stamped on a previous session start — write-once

    try:
        machine = machine_identity.get_machine_name(interactive=False)
        session_origin.write_origin_marker(session_dir, machine)
    except OSError as exc:
        print(f"session-origin-marker: failed to write origin marker: {exc}", file=sys.stderr)
    return 0
```

Register it in `HOOK_VERBS`/`HOOK_DESCRIPTIONS` (`ccst.py:79-106`), following the exact pattern of
the neighbouring `"session-tag"` entry:

```python
# ccst.py:79-91 region — add one line to HOOK_VERBS
    "session-origin": "cccs_hooks.session_origin_marker",

# ccst.py:94-106 region — add one line to HOOK_DESCRIPTIONS
    "session-origin": "Stamp new sessions with the machine that created them",
```

Add it to the `SessionStart` array in `config/hooks-bundle.json:3-32`, as a 5th entry alongside
the existing four. The `pending-migration` entry currently ends the array with no trailing
comma — add one after it before pasting this in, or the file becomes invalid JSON:

```json
,{"type":"command","command":"ccst hooks run session-origin","timeout":5,"statusMessage":"Recording session origin machine..."}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_origin_marker.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/session_origin_marker.py tests/test_session_origin_marker.py src/cc_session_tools/cli/ccst.py config/hooks-bundle.json
git commit -m "feat: add session-origin SessionStart hook"
```

---

### Task 4: `lib/sessions.py` — find an on-disk session with no `sessions.db` row

**Files:**
- Modify: `src/cc_session_tools/lib/sessions.py` — new function after `find_orphan_transcripts`
  (`sessions.py:79-129`)
- Modify: `tests/test_sessions.py`

This is the cheap, targeted lookup both `ccd` (Task 5) and `ccr` (Task 6) need: given one exact
basename, is there a `cc-sessions/<basename>/` directory on disk under any configured root with
**no** matching `sessions.db` row? Cost is bounded by the number of project directories under each
root (a handful), not the number of sessions — nothing like the rejected per-listing scan.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_sessions.py

class TestFindForeignOnDiskSession:
    def test_finds_dir_with_no_db_row(self, tmp_path):
        from cc_session_tools.lib.sessions import find_foreign_on_disk_session

        proj = tmp_path / "myproj"
        (proj / "cc-sessions" / "20260101-foo").mkdir(parents=True)
        # No sessions_db row written at all — pure filesystem presence.

        match = find_foreign_on_disk_session("20260101-foo", [tmp_path])
        assert match is not None
        assert match.basename == "20260101-foo"
        assert match.project_dir == proj

    def test_returns_none_when_no_matching_dir_exists(self, tmp_path):
        from cc_session_tools.lib.sessions import find_foreign_on_disk_session

        proj = tmp_path / "myproj"
        proj.mkdir(parents=True)

        assert find_foreign_on_disk_session("20260101-nope", [tmp_path]) is None

    def test_ignores_non_directory_entries_under_root(self, tmp_path):
        from cc_session_tools.lib.sessions import find_foreign_on_disk_session

        (tmp_path / "not-a-project.txt").write_text("x")

        assert find_foreign_on_disk_session("20260101-foo", [tmp_path]) is None

    def test_searches_every_configured_root(self, tmp_path):
        from cc_session_tools.lib.sessions import find_foreign_on_disk_session

        root_a = tmp_path / "root-a"
        root_b = tmp_path / "root-b"
        (root_b / "myproj" / "cc-sessions" / "20260101-foo").mkdir(parents=True)

        match = find_foreign_on_disk_session("20260101-foo", [root_a, root_b])
        assert match is not None
        assert match.project_dir == root_b / "myproj"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions.py -k ForeignOnDisk -v`
Expected: FAIL with `ImportError: cannot import name 'find_foreign_on_disk_session'`

- [ ] **Step 3: Write the implementation**

Add after `find_orphan_transcripts` (`sessions.py:79-129`):

```python
def find_foreign_on_disk_session(basename: str, roots: list[Path]) -> SessionMatch | None:
    """A cc-sessions/<basename>/ directory that exists on disk under one of
    `roots` — checked purely by filesystem presence, with no reference to
    sessions.db at all. Callers (ccd, ccr) combine this with their own
    sessions.db row-existence check to decide "known locally" vs "possibly
    from another machine" (multi-laptop sync spec). Bounded by the number of
    project directories under each root, not the number of sessions —
    mirrors find_orphan_transcripts's own root/project iteration shape
    exactly."""
    for root in roots:
        if not root.is_dir():
            continue
        for project_dir in root.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / "cc-sessions" / basename
            if candidate.is_dir():
                return SessionMatch(basename=basename, project_dir=project_dir, session_dir=candidate)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions.py -k ForeignOnDisk -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/sessions.py tests/test_sessions.py
git commit -m "feat: add on-disk session lookup for foreign-session detection"
```

---

### Task 5: `ccd.py` — flag a duplicate with no local `sessions.db` record

**Files:**
- Modify: `src/cc_session_tools/cli/ccd.py:175-185`
- Modify: `tests/test_cli_ccd.py`

`ccd`'s existing refusal (a same-day, same-tag directory already has real content) already fires
regardless of this feature. This task only enriches its message: if the colliding directory has no
`sessions.db` row for this exact `(project_dir, basename)`, say so — it's a strong signal the
directory synced in from another laptop rather than being a genuine same-machine duplicate.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_cli_ccd.py

def test_ccd_duplicate_refusal_notes_missing_db_record(
    fake_home, tmp_path, monkeypatch, capsys, captured_launch
):
    """A collision with no local sessions.db row is flagged as possibly
    foreign; one *with* a row (genuine same-laptop duplicate) is not."""
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    monkeypatch.chdir(proj)

    date_str = datetime.now().strftime("%Y%m%d")
    basename = f"{date_str}-mytag"
    (proj / "cc-sessions" / basename).mkdir(parents=True)
    _write_transcript(proj, basename, user_typed=True)
    # No sessions_db.ensure_session_row call — simulates a folder that
    # synced in from another laptop, never touched locally.

    rc = ccd.main(["mytag"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no local record" in err.lower()


def test_ccd_duplicate_refusal_silent_on_missing_db_note_for_genuine_local_duplicate(
    fake_home, tmp_path, monkeypatch, capsys, captured_launch
):
    from cc_session_tools.lib import sessions_db

    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    monkeypatch.chdir(proj)

    date_str = datetime.now().strftime("%Y%m%d")
    basename = f"{date_str}-mytag"
    (proj / "cc-sessions" / basename).mkdir(parents=True)
    _write_transcript(proj, basename, user_typed=True)
    sessions_db.ensure_session_row(proj, basename)  # genuine local record exists

    rc = ccd.main(["mytag"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no local record" not in err.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_ccd.py -k missing_db -v`
Expected: FAIL — the first test finds no "no local record" text; the second passes already
(vacuously true today), so only the first is a genuine red

- [ ] **Step 3: Write the implementation**

Modify the refusal block (`ccd.py:175-185`):

```python
    if session_dir.exists() and not is_empty_session(session_name, real_pwd):
        from cc_session_tools.lib import sessions_db

        known_locally = any(
            row.basename == session_name
            for row in sessions_db.list_sessions(project_dir=real_pwd)
        )
        print(
            f"ccd: session '{session_name}' already started today in this directory.",
            file=sys.stderr,
        )
        print(f"ccd:   existing: {session_dir}", file=sys.stderr)
        if not known_locally:
            print(
                "ccd:   no local record of this session in sessions.db — it may have been "
                "created on a different machine and synced in.",
                file=sys.stderr,
            )
        print(
            f"ccd: Use a different name tag, or 'ccr {tag}' to resume the existing one.",
            file=sys.stderr,
        )
        return 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_ccd.py -v`
Expected: all pass, including the 2 new tests

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccd.py tests/test_cli_ccd.py
git commit -m "feat: ccd flags a session collision with no local sessions.db record"
```

---

### Task 6: `ccr.py` refuses an on-disk-only session unless `--force`

**Files:**
- Modify: `src/cc_session_tools/cli/ccr.py` — `_build_parser` (`ccr.py:64-81`), the `if not
  matches:` branch (`ccr.py:118-120`)
- Modify: `tests/test_cli_ccr.py`

Today, `ccr <exact-basename>` finds nothing at all for a directory that exists on disk with no
`sessions.db` row (`find_matching_sessions` is 100% DB-driven — confirmed, no existing code path
covers this). This task adds exactly one new branch, reached only when the normal DB-driven search
already came up empty for a fragment that looks like a full basename.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_cli_ccr.py

def _make_foreign_session_dir(repos: Path, project: str, basename: str) -> Path:
    """A cc-sessions/<basename>/ directory with NO sessions_db row at all —
    simulates a folder synced in from another laptop, never opened here."""
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    (sess / "out").mkdir()
    return sess


def test_ccr_refuses_ondisk_session_with_no_db_row(fake_repos, captured_launch, capsys):
    _make_foreign_session_dir(fake_repos, "myproj", "20260504-foo")
    rc = ccr.main(["20260504-foo"])
    assert rc == 1
    assert "cmd" not in captured_launch
    err = capsys.readouterr().err
    assert "no local record" in err.lower()


def test_ccr_force_resumes_ondisk_session_with_no_db_row(fake_repos, captured_launch):
    _make_foreign_session_dir(fake_repos, "myproj", "20260504-foo")
    rc = ccr.main(["20260504-foo", "--force"])
    assert rc == 0
    assert captured_launch["cmd"][0] == "claude"


def test_ccr_message_includes_origin_marker_when_present(fake_repos, captured_launch, capsys):
    from cc_session_tools.lib import session_origin

    sess = _make_foreign_session_dir(fake_repos, "myproj", "20260504-foo")
    session_origin.write_origin_marker(sess, "other-laptop")

    rc = ccr.main(["20260504-foo"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "other-laptop" in err


def test_ccr_normal_local_resume_unaffected(fake_repos, captured_launch):
    """A session WITH a sessions_db row resumes exactly as before — this
    feature only changes the zero-DB-match case."""
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    rc = ccr.main(["foo-bar"])
    assert rc == 0
    assert captured_launch["cmd"][0] == "claude"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_ccr.py -k "ondisk or origin_marker_when_present" -v`
Expected: FAIL — `--force` is an unrecognized argument; the refusal tests currently get "no
sessions match" instead of resuming or the new message

- [ ] **Step 3: Write the implementation**

Add `--force` to `_build_parser` (`ccr.py:64-81`):

```python
    p.add_argument(
        "--force",
        action="store_true",
        help="Resume even if the session has no local sessions.db record (it may have been "
             "created on a different laptop).",
    )
```

Import the new lookup at the top of `ccr.py`, alongside the existing `sessions` imports:

```python
from cc_session_tools.lib.sessions import (
    SESSION_FULL_RE, SessionMatch, find_all_jsonls_for_session, find_foreign_on_disk_session,
    find_matching_sessions, find_orphan_transcripts, session_tag,
)
```

Add a small message helper near `_pick_duplicate_transcript`:

```python
def _foreign_session_message(fragment: str, match: SessionMatch) -> str:
    from cc_session_tools.lib import session_origin

    machine = session_origin.read_origin_marker(match.session_dir)
    origin = f" (origin marker: '{machine}')" if machine else ""
    return (
        f"ccr: found 'cc-sessions/{fragment}/' on disk under {match.project_dir}{origin}, but "
        "no local record of it in sessions.db — it may have been created on a different "
        "machine (transcripts don't sync, so cross-laptop resume isn't supported). "
        "Pass --force to attempt it anyway."
    )
```

Modify the `if not matches:` branch (`ccr.py:118-120`):

```python
    if not matches:
        if SESSION_FULL_RE.fullmatch(args.fragment):
            foreign = find_foreign_on_disk_session(args.fragment, roots)
            if foreign is not None:
                if not args.force:
                    print(_foreign_session_message(args.fragment, foreign), file=sys.stderr)
                    return 1
                matches = [foreign]
        if not matches:
            print(f"ccr: no sessions match '{args.fragment}'", file=sys.stderr)
            return 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_ccr.py -v`
Expected: all pass, including the 4 new tests

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccr.py tests/test_cli_ccr.py
git commit -m "feat: ccr refuses an on-disk session with no local record unless --force"
```

---

## Verification

- [ ] **Run the full test suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: all tests pass — in particular, confirm
`tests/test_ccs_sentinel_sort.py::TestSessionEnumerationScaling` still passes unchanged (this plan
touches nothing in `ccs.py`, so it should never have been at risk under this design)

- [ ] **Run the linter/type-checker if configured**

Run: `uv run ruff check src/ tests/` (and `uv run mypy src/` if configured)
Expected: no new errors introduced by this plan's files

- [ ] **Manual smoke test**

```bash
uv run python -m cc_session_tools.cli.ccst hooks install --apply   # picks up the new hook
cd ~/cc/some-project && uv run python -m cc_session_tools.cli.ccd smoketest
cat cc-sessions/<generated-name>/origin-machine.txt   # should show this machine's name
# Simulate "the other laptop": delete the sessions.db row for it, then:
uv run python -m cc_session_tools.cli.ccr <generated-name>          # should refuse
uv run python -m cc_session_tools.cli.ccr <generated-name> --force  # should proceed
```

## Handoff

Independent of Plans 1 and 3 — no shared files with either. This revision was not re-run through
the plan-document-reviewer sub-skill given session context constraints at the time it was written
— recommend one more review pass (ideally in a fresh/compacted session) before implementation
begins, given how much changed from the originally-reviewed version.
