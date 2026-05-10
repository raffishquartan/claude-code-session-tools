# CCX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 14 improvements to the ccs/ccr/ccd CLI tools: interactive session picker, iterative ETA, transcript search, date filters, JSON output, exact-match fast-path, hook filtering, OSC 8 links, flag pass-through, debug mode, and more.

**Architecture:** New shared library modules (`lib/picker.py`, `lib/debug.py`, `lib/claude_flags.py`) are built first; each CLI tool (`ccs.py`, `ccr.py`, `ccd.py`) is improved in turn. All changes are test-driven. The interactive picker in both ccs and ccr shares a single `pick_from_list` function and, after a pick, ccs exec()s into ccr which handles the resume.

**Tech Stack:** Python 3.10+, pytest, ripgrep (optional), difflib (stdlib), re (stdlib)

**Spec:** `docs/superpowers/specs/2026-05-10-ccx-improvements-design.md`

---

## Task 0: Worktree setup

- [ ] **Step 1: Create feature branch and worktree**

```bash
git worktree add ../ccx-improvements-wt f/20260510-ccx-improvements --checkout -b f/20260510-ccx-improvements
cd ../ccx-improvements-wt
```

- [ ] **Step 2: Verify environment**

```bash
uv run pytest tests/ -q --tb=short
```

Expected: all existing tests pass.

---

## Task 1: `lib/debug.py` — CCX_DEBUG support

All three tools (ccs, ccr, ccd) use this. Build it first so later tasks can import it.

**Files:**
- Create: `src/cc_session_tools/lib/debug.py`
- Create: `tests/test_debug.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_debug.py
from __future__ import annotations
import sys
import pytest
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
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_debug.py -v
```

- [ ] **Step 3: Implement**

```python
# src/cc_session_tools/lib/debug.py
from __future__ import annotations

import os
import sys


def is_debug() -> bool:
    return os.environ.get("CCX_DEBUG", "").strip() not in ("", "0")


def debug(*args: object) -> None:
    if is_debug():
        print("[CCX_DEBUG]", *args, file=sys.stderr)
```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_debug.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/debug.py tests/test_debug.py
git commit -m "feat: add lib/debug.py for CCX_DEBUG env-var support"
```

---

## Task 2: `lib/picker.py` — shared interactive session picker

**Files:**
- Create: `src/cc_session_tools/lib/picker.py`
- Create: `tests/test_picker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_picker.py
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
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_picker.py -v
```

- [ ] **Step 3: Implement**

```python
# src/cc_session_tools/lib/picker.py
from __future__ import annotations


def pick_from_list(labels: list[str]) -> int | None:
    """Display a 1-9/0 numbered menu. Returns 0-based index or None if cancelled.

    Requires 1 <= len(labels) <= 10. Reads one line from stdin.
    """
    assert 1 <= len(labels) <= 10
    for i, label in enumerate(labels):
        num = (i + 1) if i < 9 else 0
        print(f"  {num}) {label}")
    n = len(labels)
    range_str = f"1-{min(n, 9)}" + (", 0" if n == 10 else "")
    try:
        raw = input(f"Pick [{range_str}, q to cancel]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw or raw[0].lower() == "q":
        return None
    if raw[0].isdigit():
        d = int(raw[0])
        idx = d - 1 if d != 0 else 9
        if 0 <= idx < n:
            return idx
    return None
```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_picker.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/picker.py tests/test_picker.py
git commit -m "feat: add lib/picker.py shared 1-9/0 session picker"
```

---

## Task 3: `lib/sessions.py` — `transcript_dir_for_project`

**Files:**
- Modify: `src/cc_session_tools/lib/sessions.py` (add function at end)
- Modify: `tests/test_sessions.py` (add tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_sessions.py`:

```python
from pathlib import Path
from cc_session_tools.lib.sessions import transcript_dir_for_project


def test_transcript_dir_encoding_simple():
    # /home/alice/repos/my-project -> -home-chris-repos-my-project
    result = transcript_dir_for_project(Path("/home/alice/repos/my-project"))
    assert result == Path.home() / ".claude" / "projects" / "-home-chris-repos-my-project"


def test_transcript_dir_encoding_with_dots():
    # Dots are also replaced with dashes
    result = transcript_dir_for_project(Path("/home/alice/.local/share"))
    assert result == Path.home() / ".claude" / "projects" / "-home-chris--local-share"


def test_transcript_dir_returns_path_object():
    result = transcript_dir_for_project(Path("/tmp/foo"))
    assert isinstance(result, Path)
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_sessions.py -k "transcript" -v
```

- [ ] **Step 3: Implement**

Add to the end of `src/cc_session_tools/lib/sessions.py`:

```python
def transcript_dir_for_project(project_dir: Path) -> Path:
    """Return the ~/.claude/projects/<encoded> directory for a project.

    Encoding: each '/' and '.' in the absolute project path is replaced with '-'.
    Does not check whether the directory exists.
    """
    encoded = str(project_dir.resolve()).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / encoded
```

Also add to `__all__` in `sessions.py` if it exists, or just export normally.

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_sessions.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/sessions.py tests/test_sessions.py
git commit -m "feat: add transcript_dir_for_project to lib/sessions"
```

---

## Task 4: `lib/claude_flags.py` — runtime claude flag enumeration

**Files:**
- Create: `src/cc_session_tools/lib/claude_flags.py`
- Create: `tests/test_claude_flags.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_claude_flags.py
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from cc_session_tools.lib import claude_flags


SAMPLE_HELP = """
Usage: claude [options]

Options:
  --model <model>   Model to use
  --debug           Enable debug
  -p, --print       Print and exit
  --append-system-prompt <p>  Append system prompt
  -h, --help        Display help
"""


def test_get_claude_flags_parses_long_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_flags, "_CACHE_FILE", tmp_path / "flags.json")
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("pathlib.Path.stat") as mock_stat, \
         patch("subprocess.run") as mock_run:
        mock_stat.return_value = MagicMock(st_mtime=123.0)
        mock_run.return_value = MagicMock(stdout=SAMPLE_HELP, stderr="", returncode=0)
        flags = claude_flags.get_claude_flags()
    assert "--model" in flags
    assert "--debug" in flags
    assert "--append-system-prompt" in flags
    assert "--help" in flags
    assert "-p" not in flags  # short flags excluded


def test_get_claude_flags_uses_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / "flags.json"
    monkeypatch.setattr(claude_flags, "_CACHE_FILE", cache_file)
    cache_data = {"mtime": 999.0, "path": "/usr/bin/claude", "flags": ["--model", "--debug"]}
    cache_file.write_text(json.dumps(cache_data))
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("pathlib.Path.stat") as mock_stat, \
         patch("subprocess.run") as mock_run:
        mock_stat.return_value = MagicMock(st_mtime=999.0)
        flags = claude_flags.get_claude_flags()
        mock_run.assert_not_called()
    assert "--model" in flags


def test_get_claude_flags_returns_empty_if_claude_missing(monkeypatch):
    with patch("shutil.which", return_value=None):
        flags = claude_flags.get_claude_flags()
    assert flags == set()
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_claude_flags.py -v
```

- [ ] **Step 3: Implement**

```python
# src/cc_session_tools/lib/claude_flags.py
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


_CACHE_DIR = Path.home() / ".cache" / "cc-session-tools"
_CACHE_FILE = _CACHE_DIR / "claude-flags.json"


def get_claude_flags() -> set[str]:
    """Return set of valid long-form claude flags (e.g. {'--model', '--debug'}).

    Parses `claude --help` at runtime; cached by binary mtime.
    Returns empty set if claude is not on PATH or help parse fails.
    """
    claude = shutil.which("claude")
    if not claude:
        return set()

    try:
        mtime = Path(claude).stat().st_mtime
    except OSError:
        return set()

    if _CACHE_FILE.exists():
        try:
            cached = json.loads(_CACHE_FILE.read_text())
            if cached.get("mtime") == mtime and cached.get("path") == claude:
                return set(cached["flags"])
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    try:
        result = subprocess.run(
            ["claude", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        text = result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return set()

    # Match --flag-name at word boundaries, exclude short -f flags
    flags = set(re.findall(r"(?<!\w)(--[\w-]+)", text))

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"mtime": mtime, "path": claude, "flags": sorted(flags)})
        )
    except OSError:
        pass

    return flags
```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_claude_flags.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/claude_flags.py tests/test_claude_flags.py
git commit -m "feat: add lib/claude_flags.py for runtime claude flag enumeration"
```

---

## Task 5: `ccs` — `--exclude-hooks` flag

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_cli_ccs.py`

A session is a "hook session" if its basename tag (part after `YYYYMMDD-`) contains the word `hook` (case-insensitive). Uses existing `session_tag()` from `lib/sessions.py`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_cli_ccs.py`:

```python
def test_exclude_hooks_hides_hook_sessions(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-hook-security-check")
    _make_session(fake_repos, "myproj", "20260504-normal-work")
    monkeypatch.chdir(proj)

    rc = ccs.main(["2026", "--exclude-hooks"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "normal-work" in out
    assert "hook-security-check" not in out


def test_exclude_hooks_reports_count_on_stderr(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-hook-security-check")
    _make_session(fake_repos, "myproj", "20260504-normal-work")
    monkeypatch.chdir(proj)

    ccs.main(["2026", "--exclude-hooks"])
    err = capsys.readouterr().err
    assert "1 hook" in err
    # Note: no "--include-hooks" hint in message (flag not implemented)


def test_without_exclude_hooks_includes_hook_sessions_by_default(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-hook-security-check")
    monkeypatch.chdir(proj)

    rc = ccs.main(["hook"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hook-security-check" in out
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "hook" -v
```

- [ ] **Step 3: Implement**

In `ccs.py`:
1. Add `--exclude-hooks` flag to `_build_parser()`:
   ```python
   p.add_argument("--exclude-hooks", action="store_true",
                  help="Exclude sessions whose tag contains 'hook' "
                       "(e.g. hook-security-check sessions).")
   ```
2. Add helper function:
   ```python
   def _is_hook_session(basename: str) -> bool:
       from cc_session_tools.lib.sessions import session_tag
       tag = session_tag(basename)
       return tag is not None and "hook" in tag.lower()
   ```
3. In `main()`, after building `sessions` list, filter when `args.exclude_hooks`:
   ```python
   if args.exclude_hooks:
       before = len(sessions)
       sessions = [(s, p) for s, p in sessions if not _is_hook_session(s.name)]
       excluded = before - len(sessions)
       if excluded:
           noun = "session" if excluded == 1 else "sessions"
           print(
               f"ccs: excluded {excluded} hook {noun}",
               file=sys.stderr,
           )
   ```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_cli_ccs.py
git commit -m "feat(ccs): add --exclude-hooks to filter hook-security sessions"
```

---

## Task 6: `ccs` — `--since`, `--before`, `--days` date filters

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_cli_ccs.py`

- [ ] **Step 1: Write failing tests**

```python
def test_since_filter_excludes_old_sessions(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260101-old-work")
    _make_session(fake_repos, "myproj", "20260504-new-work")
    monkeypatch.chdir(proj)

    rc = ccs.main(["work", "--since", "20260301"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "new-work" in out
    assert "old-work" not in out


def test_before_filter_excludes_new_sessions(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260101-old-work")
    _make_session(fake_repos, "myproj", "20260504-new-work")
    monkeypatch.chdir(proj)

    rc = ccs.main(["work", "--before", "20260301"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "old-work" in out
    assert "new-work" not in out


def test_days_filter_keeps_recent_sessions(fake_repos, monkeypatch, capsys):
    import datetime
    proj = fake_repos / "myproj"
    today = datetime.date.today()
    yesterday = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")
    old = "20200101"
    _make_session(fake_repos, "myproj", f"{yesterday}-recent-work")
    _make_session(fake_repos, "myproj", f"{old}-ancient-work")
    monkeypatch.chdir(proj)

    rc = ccs.main(["work", "--days", "7"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "recent-work" in out
    assert "ancient-work" not in out


def test_invalid_since_date_exits_with_error(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    monkeypatch.chdir(proj)
    rc = ccs.main(["work", "--since", "not-a-date"])
    assert rc == 1
    assert "invalid date" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "filter or since or before or days" -v
```

- [ ] **Step 3: Implement**

1. Add to `_build_parser()`:
   ```python
   p.add_argument("--since", metavar="YYYYMMDD",
                  help="Include only sessions started on or after this date.")
   p.add_argument("--before", metavar="YYYYMMDD",
                  help="Include only sessions started before this date.")
   p.add_argument("--days", type=int, metavar="N",
                  help="Include only sessions started within the last N days.")
   ```

2. Add helper `_parse_date_filter(args)` that validates and returns a `(since_key, before_key)` tuple (both `str | None`, in `YYYYMMDD` format):
   ```python
   import datetime

   def _parse_date_filter(args) -> tuple[str | None, str | None]:
       since = None
       before = None
       if args.days is not None:
           cutoff = datetime.date.today() - datetime.timedelta(days=args.days)
           since = cutoff.strftime("%Y%m%d")
       if args.since is not None:
           try:
               datetime.datetime.strptime(args.since, "%Y%m%d")
           except ValueError:
               print(f"ccs: invalid date '{args.since}' (expected YYYYMMDD)", file=sys.stderr)
               return None, None  # signal error
           since = args.since
       if args.before is not None:
           try:
               datetime.datetime.strptime(args.before, "%Y%m%d")
           except ValueError:
               print(f"ccs: invalid date '{args.before}' (expected YYYYMMDD)", file=sys.stderr)
               return None, None
           before = args.before
       return since, before
   ```

3. In `main()`, after building `sessions`, apply the filter:
   ```python
   since_key, before_key = _parse_date_filter(args)
   if since_key is None and (args.since or args.before):  # parse error
       return 1
   sessions = [
       (s, p) for s, p in sessions
       if (since_key is None or session_start_date(s.name) >= since_key)
       and (before_key is None or session_start_date(s.name) < before_key)
   ]
   ```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_cli_ccs.py
git commit -m "feat(ccs): add --since, --before, --days date-range filters"
```

---

## Task 7: `ccs` — `--json` and `--null` output

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_cli_ccs.py`

- [ ] **Step 1: Write failing tests**

```python
import json as json_mod

def test_json_output_name_search(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    _make_session(fake_repos, "myproj", "20260503-foo-baz")
    proj = fake_repos / "myproj"
    monkeypatch.chdir(proj)

    rc = ccs.main(["foo", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json_mod.loads(out)
    assert isinstance(data, list)
    assert len(data) == 2
    basenames = {d["basename"] for d in data}
    assert "20260504-foo-bar" in basenames
    assert all("project_dir" in d for d in data)


def test_null_output_name_search(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    proj = fake_repos / "myproj"
    monkeypatch.chdir(proj)

    rc = ccs.main(["foo", "--null"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo-bar\x00" in out


def test_json_no_results_returns_empty_array(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-unrelated")
    monkeypatch.chdir(proj)

    rc = ccs.main(["zzznomatch", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json_mod.loads(out) == []
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "json or null" -v
```

- [ ] **Step 3: Implement**

1. Add to `_build_parser()`:
   ```python
   fmt = p.add_mutually_exclusive_group()
   fmt.add_argument("--json", action="store_true",
                    help="Output results as a JSON array.")
   fmt.add_argument("--null", action="store_true",
                    help="Output null-delimited basenames (for xargs -0).")
   ```

2. Add output function:
   ```python
   def _output_machine_readable(results: list[_Result], do_null: bool) -> None:
       import json as _json
       if do_null:
           for r in results:
               sys.stdout.write(r.basename + "\x00")
       else:
           data = [
               {
                   "basename": r.basename,
                   "project_dir": str(r.project_dir),
                   "context_lines": r.context_lines,
               }
               for r in results
           ]
           print(_json.dumps(data))
   ```

3. In `_name_search`, check for machine-readable mode before printing/picking:
   ```python
   def _name_search(sessions, query, do_global, *, do_json=False, do_null=False):
       ...
       if do_json or do_null:
           _output_machine_readable(results, do_null)
           return 0
       # existing display + picker logic
   ```

4. Pass `do_json=args.json, do_null=args.null` from `main()`.

5. Same pattern for `_contents_search` functions.

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_cli_ccs.py
git commit -m "feat(ccs): add --json and --null machine-readable output"
```

---

## Task 8: `ccs` — `CCS_DEFAULT_GLOBAL=1` and `--local`

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_cli_ccs.py`

- [ ] **Step 1: Write failing tests**

```python
def test_default_global_env_var_enables_global_scope(fake_repos, monkeypatch, capsys):
    # Two projects, search from proj1 without --global.
    # _make_session uses mkdir(parents=True) so proj2 is created automatically.
    _make_session(fake_repos, "proj1", "20260504-proj1-session")
    _make_session(fake_repos, "proj2", "20260504-proj2-session")
    proj1 = fake_repos / "proj1"
    monkeypatch.chdir(proj1)
    monkeypatch.setenv("CCS_DEFAULT_GLOBAL", "1")

    rc = ccs.main(["session"])
    assert rc == 0
    out = capsys.readouterr().out
    # Both projects should appear when global is default
    assert "proj1-session" in out
    assert "proj2-session" in out


def test_local_flag_overrides_default_global(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "proj1", "20260504-proj1-session")
    _make_session(fake_repos, "proj2", "20260504-proj2-session")
    proj1 = fake_repos / "proj1"
    monkeypatch.chdir(proj1)
    monkeypatch.setenv("CCS_DEFAULT_GLOBAL", "1")

    rc = ccs.main(["session", "--local"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "proj1-session" in out
    assert "proj2-session" not in out
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "global or local" -v
```

- [ ] **Step 3: Implement**

1. Add `--local` to parser:
   ```python
   p.add_argument("--local", action="store_true",
                  help="Search only current directory's sessions "
                       "(overrides CCS_DEFAULT_GLOBAL=1).")
   ```

2. In `main()`, compute effective global flag:
   ```python
   import os
   effective_global = args.do_global or (
       os.environ.get("CCS_DEFAULT_GLOBAL", "").strip() not in ("", "0")
       and not args.local
   )
   ```

3. Replace `args.do_global` with `effective_global` in the `_collect_pairs` call.

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_cli_ccs.py
git commit -m "feat(ccs): add CCS_DEFAULT_GLOBAL env var and --local override"
```

---

## Task 9: `ccs` — "Did you mean?" on zero results

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_cli_ccs.py`

- [ ] **Step 1: Write failing tests**

```python
def test_did_you_mean_suggests_close_match(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-config-cleanup")
    monkeypatch.chdir(proj)

    rc = ccs.main(["confg-cleanup"])  # typo
    assert rc == 1
    err = capsys.readouterr().err
    assert "did you mean" in err.lower()
    assert "config-cleanup" in err


def test_no_suggestion_when_completely_unrelated(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-config-cleanup")
    monkeypatch.chdir(proj)

    rc = ccs.main(["zzzzzzz"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "did you mean" not in err.lower()
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "mean" -v
```

- [ ] **Step 3: Implement**

In `_name_search`, after printing "no sessions match":

```python
import difflib
all_basenames = [s.name for s, _ in sessions]
suggestions = difflib.get_close_matches(query, all_basenames, n=3, cutoff=0.4)
if suggestions:
    print(f"ccs: did you mean: {', '.join(suggestions)}?", file=sys.stderr)
```

Pass `sessions` as a parameter to `_name_search` (already available via closure in current code - may need minor refactor to pass `all_sessions` for suggestions while keeping filtered `results`).

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_cli_ccs.py
git commit -m "feat(ccs): add 'did you mean?' suggestion on zero results"
```

---

## Task 10: `ccs` — OSC 8 terminal hyperlinks

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_cli_ccs.py`

- [ ] **Step 1: Write failing tests**

```python
def test_osc8_link_wraps_path_in_escape_sequence():
    from pathlib import Path
    from cc_session_tools.cli.ccs import _osc8_link
    path = Path("/tmp/my-session")
    result = _osc8_link("my-session", path)
    assert "\033]8;;" in result
    assert "my-session" in result
    assert result.endswith("\033]8;;\033\\")


def test_name_search_no_osc8_in_non_tty(fake_repos, monkeypatch, capsys):
    # capsys stdout is not a TTY, so no OSC 8 should appear
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo")
    monkeypatch.chdir(proj)
    rc = ccs.main(["foo"])
    out = capsys.readouterr().out
    assert "\033]8" not in out
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "osc8" -v
```

- [ ] **Step 3: Implement**

Add to `ccs.py`:

```python
def _osc8_link(text: str, path: Path) -> str:
    uri = path.as_uri()
    return f"\033]8;;{uri}\033\\{text}\033]8;;\033\\"


def _maybe_link(text: str, path: Path) -> str:
    """Wrap text in OSC 8 hyperlink if stdout is a TTY and NO_COLOR is not set."""
    if (
        sys.stdout.isatty()
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM") != "dumb"
    ):
        return _osc8_link(text, path)
    return text
```

In `_name_search` and `_print_results`, wrap the basename:

```python
display_name = _maybe_link(r.basename, r.project_dir / "cc-sessions" / r.basename)
```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_cli_ccs.py
git commit -m "feat(ccs): add OSC 8 terminal hyperlinks on session basenames"
```

---

## Task 11: `ccs --contents` — search Claude transcript JSONL files

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_cli_ccs.py`

When building the list of directories to search, also include each project's `~/.claude/projects/<encoded>/` directory alongside the `cc-sessions/<tag>/` directories.

- [ ] **Step 1: Write failing tests**

```python
def test_contents_search_includes_transcript_dir(fake_repos, fake_home, monkeypatch, capsys, force_grep_path):
    # Create session and matching transcript in ~/.claude/projects/
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo", contents="normal content")
    # Simulate a transcript dir
    from cc_session_tools.lib.sessions import transcript_dir_for_project
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True)
    (t_dir / "abc123.jsonl").write_text('{"text": "unique-transcript-string"}')
    monkeypatch.chdir(proj)

    rc = ccs.main(["unique-transcript-string", "--contents"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo" in out
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "transcript" -v
```

- [ ] **Step 3: Implement**

**In `_contents_search_with_rg`** (the rg batch path):

The function builds `sess_by_dir: dict[str, tuple[Path, Path]]` mapping resolved session paths to `(sess, proj)`. Extend it to also register each project's transcript directory under the same `(sess, proj)` value:

```python
from cc_session_tools.lib.sessions import transcript_dir_for_project

sess_by_dir: dict[str, tuple[Path, Path]] = {}
for sess, proj in sessions:
    key = str(sess.resolve())
    sess_by_dir[key] = (sess, proj)
    t_dir = transcript_dir_for_project(proj)
    if t_dir.is_dir():
        sess_by_dir[str(t_dir.resolve())] = (sess, proj)  # same (sess, proj) as session
```

Pass both session dirs AND transcript dirs as rg targets (already works because `sess_by_dir.keys()` is used as the target list in the existing `_rg_cmd` call). The existing grouping loop (which finds the longest matching key prefix in each rg output line) will then correctly attribute transcript matches back to the right session - no changes needed to the grouping logic itself.

**In `_contents_search_with_grep`** (the grep fallback path):

`enumerate_session_files(sess, ...)` currently walks only `sess`. Extend it to also walk the transcript dir:

```python
t_dir = transcript_dir_for_project(proj)
files, bytes_, skipped = enumerate_session_files(sess, max_bytes=max_bytes)
if t_dir.is_dir():
    t_files, t_bytes, t_skipped = enumerate_session_files(t_dir, max_bytes=max_bytes)
    files += t_files
    bytes_ += t_bytes
    skipped += t_skipped
```

Then pass the combined `files` to `grep_files` as before.

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_cli_ccs.py
git commit -m "feat(ccs): include ~/.claude/projects transcript JSONL in --contents search"
```

---

## Task 12: `ccs --contents` — batched rg ETA

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Create: `tests/test_ccs_eta.py`

Replace the single-invocation rg path with three sequential batched invocations. After each batch, print an updated total-time estimate using `X + (X/Y)*(Z-Y)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ccs_eta.py
from __future__ import annotations
from cc_session_tools.cli.ccs import _compute_eta, _batch_sizes


def test_compute_eta_at_halfway():
    # X=5s, Y=5 done, Z=10 total → 5 + (5/5)*(10-5) = 10s
    assert _compute_eta(elapsed=5.0, completed=5, total=10) == pytest.approx(10.0)


def test_compute_eta_at_start():
    # X=1s, Y=1 done, Z=100 total → 1 + (1/1)*(99) = 100s
    assert _compute_eta(elapsed=1.0, completed=1, total=100) == pytest.approx(100.0)


def test_batch_sizes_small():
    # Z <= 10: one batch
    assert _batch_sizes(5) == [5]
    assert _batch_sizes(10) == [10]


def test_batch_sizes_medium():
    # 10 < Z <= 110: two batches
    assert _batch_sizes(50) == [10, 40]
    assert _batch_sizes(110) == [10, 100]


def test_batch_sizes_large():
    # Z > 110: three batches
    assert _batch_sizes(200) == [10, 100, 90]
    assert _batch_sizes(111) == [10, 100, 1]
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_ccs_eta.py -v
```

- [ ] **Step 3: Implement**

Add to `ccs.py`:

```python
def _compute_eta(elapsed: float, completed: int, total: int) -> float:
    """Total estimated time: elapsed + (elapsed/completed)*(remaining)."""
    if completed <= 0:
        return float("inf")
    remaining = total - completed
    return elapsed + (elapsed / completed) * remaining


def _batch_sizes(total: int) -> list[int]:
    """Return batch sizes for the three-phase rg strategy."""
    if total <= 10:
        return [total]
    if total <= 110:
        return [10, total - 10]
    return [10, 100, total - 110]
```

Restructure `_contents_search_with_rg` to:
1. Compute `batches = _batch_sizes(len(sessions))`
2. Keep `start = time.monotonic()` and `total_elapsed = 0`, `completed = 0`
3. For each batch `b` in batches:
   - Run one rg invocation on `sessions[offset:offset+b]`
   - Append output lines to `all_output_lines`
   - Update `completed += b`, `total_elapsed = time.monotonic() - start`
   - If not the last batch: compute and print ETA
4. After all batches: display final elapsed and results

Also update the grep path's ETA display from remaining-time to total-time using `_compute_eta`.

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_ccs_eta.py tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_ccs_eta.py
git commit -m "feat(ccs): batched rg with iterative ETA (X + X/Y*(Z-Y) formula)"
```

---

## Task 13: `ccr` — exact-match fast-path

**Files:**
- Modify: `src/cc_session_tools/cli/ccr.py`
- Modify: `tests/test_cli_ccr.py`

- [ ] **Step 1: Write failing tests**

```python
def test_ccr_exact_basename_skips_substring_search(fake_repos, captured_launch):
    # Create two sessions where one basename is a substring of the other
    _make_session(fake_repos, "proj1", "20260504-foo")
    _make_session(fake_repos, "proj2", "20260504-foo-bar")  # "foo" is substring of this
    
    # Passing exact basename "20260504-foo" should match only that one
    rc = ccr.main(["20260504-foo"])
    assert rc == 0
    assert "20260504-foo" in captured_launch["cmd"]
    # Should NOT match "20260504-foo-bar" even though "20260504-foo" is in it
    assert "20260504-foo-bar" not in captured_launch["cmd"]


def test_ccr_falls_back_to_substring_when_no_exact_match(fake_repos, captured_launch):
    _make_session(fake_repos, "proj1", "20260504-improve-ccx")
    
    # "improve" is not a full basename, but is a substring
    rc = ccr.main(["improve"])
    assert rc == 0
    assert "20260504-improve-ccx" in captured_launch["cmd"]
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccr.py -k "exact" -v
```

- [ ] **Step 3: Implement**

In `ccr.main()`, before calling `find_matching_sessions`:

```python
from cc_session_tools.lib.sessions import SESSION_FULL_RE

# Attempt exact-match fast-path: check if fragment looks like a full basename
exact_match = None
if SESSION_FULL_RE.fullmatch(args.fragment):
    # Fragment looks like a full basename (YYYYMMDD-tag). Try exact directory lookup.
    for root in roots:
        if not root.is_dir():
            continue
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            candidate = proj / "cc-sessions" / args.fragment
            if candidate.is_dir():
                from cc_session_tools.lib.sessions import SessionMatch
                exact_match = SessionMatch(
                    basename=args.fragment,
                    project_dir=proj,
                    session_dir=candidate,
                )
                break
        if exact_match:
            break
    # If SESSION_FULL_RE matched but no directory found, fall through to substring search.

matches = [exact_match] if exact_match else find_matching_sessions(args.fragment, roots)
```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccr.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccr.py tests/test_cli_ccr.py
git commit -m "feat(ccr): exact-match fast-path skips enumeration for full basenames"
```

---

## Task 14: `ccr` — PATH check for claude binary

**Files:**
- Modify: `src/cc_session_tools/cli/ccr.py`
- Modify: `tests/test_cli_ccr.py`

- [ ] **Step 1: Write failing tests**

```python
def test_ccr_fails_clearly_when_claude_not_on_path(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "proj1", "20260504-foo")
    # Make shutil.which('claude') return None
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    
    rc = ccr.main(["foo"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "claude" in err.lower()
    assert "not found" in err.lower() or "path" in err.lower()
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccr.py -k "path" -v
```

- [ ] **Step 3: Implement**

In `ccr.main()`, after resolving `m` (the single match), before `launch_claude_resume`:

```python
import shutil as _shutil
if not _shutil.which("claude"):
    print(
        "ccr: 'claude' not found on PATH - is Claude Code installed?",
        file=sys.stderr,
    )
    return 1
```

Note: `shutil` is already imported at the top of `ccr.py` - check and add import if missing.

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccr.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccr.py tests/test_cli_ccr.py
git commit -m "feat(ccr): fail fast with clear message when claude not on PATH"
```

---

## Task 15: `ccr` — claude flag pass-through

**Files:**
- Modify: `src/cc_session_tools/cli/ccr.py`
- Modify: `tests/test_cli_ccr.py`

Uses `lib/claude_flags.py` from Task 4.

- [ ] **Step 1: Write failing tests**

```python
def test_ccr_passes_through_valid_claude_flags(fake_repos, captured_launch, monkeypatch):
    _make_session(fake_repos, "proj1", "20260504-foo")
    # Mock get_claude_flags to return a known set
    import cc_session_tools.cli.ccr as ccr_mod
    monkeypatch.setattr(
        "cc_session_tools.lib.claude_flags.get_claude_flags",
        lambda: {"--model", "--debug", "--append-system-prompt"},
    )
    
    rc = ccr.main(["foo", "--model", "sonnet"])
    assert rc == 0
    assert "--model" in captured_launch["cmd"]
    assert "sonnet" in captured_launch["cmd"]


def test_ccr_rejects_unknown_claude_flags(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "proj1", "20260504-foo")
    monkeypatch.setattr(
        "cc_session_tools.lib.claude_flags.get_claude_flags",
        lambda: {"--model", "--debug"},
    )
    
    rc = ccr.main(["foo", "--not-a-real-flag"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "--not-a-real-flag" in err
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccr.py -k "flag" -v
```

- [ ] **Step 3: Implement**

1. Change `_build_parser` to use `parse_known_args` by switching `main()` to:
   ```python
   args, remainder = _build_parser().parse_known_args(argv)
   ```

2. After resolving the session match and before `launch_claude_resume`, validate remainder:
   ```python
   from cc_session_tools.lib.claude_flags import get_claude_flags
   if remainder:
       valid_flags = get_claude_flags()
       for arg in remainder:
           if arg.startswith("--"):
               if arg.split("=")[0] not in valid_flags:
                   print(
                       f"ccr: unknown flag '{arg}'; not a recognised claude option",
                       file=sys.stderr,
                   )
                   return 1
       cmd.extend(remainder)
   ```

3. Short flags (`-x`): pass through without validation.

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccr.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccr.py tests/test_cli_ccr.py
git commit -m "feat(ccr): validate and pass through recognised claude flags"
```

---

## Task 16: `ccs` — session picker integration

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_cli_ccs.py`

Uses `lib/picker.py` from Task 2. When name-search or --contents returns 1-10 sessions and stdin is a TTY: show picker; on selection, `os.execvp('ccr', ['ccr', basename])`.

- [ ] **Step 1: Write failing tests**

```python
def test_ccs_picker_shown_for_small_result_set(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    for i in range(3):
        _make_session(fake_repos, "myproj", f"2026050{i+1}-foo-{i}")
    monkeypatch.chdir(proj)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    
    # Mock pick_from_list to return None (cancel) and os.execvp to record call
    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda _: None)
    
    rc = ccs.main(["foo"])
    # After cancel (None), should exit cleanly
    assert rc == 0


def test_ccs_picker_execvp_on_selection(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo-one")
    _make_session(fake_repos, "myproj", "20260503-foo-two")
    monkeypatch.chdir(proj)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    
    captured_exec = {}
    def fake_execvp(name, args):
        captured_exec["name"] = name
        captured_exec["args"] = args
    
    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda _: 0)  # pick first
    monkeypatch.setattr("os.execvp", fake_execvp)
    
    ccs.main(["foo"])
    assert captured_exec.get("name") == "ccr"
    assert "20260504-foo-one" in captured_exec.get("args", [])


def test_ccs_no_picker_for_more_than_10(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    for i in range(11):
        _make_session(fake_repos, "myproj", f"202605{i+1:02d}-foo-{i:02d}")
    monkeypatch.chdir(proj)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    
    pick_called = []
    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda _: pick_called.append(1) or None)
    
    rc = ccs.main(["foo"])
    assert rc == 0
    assert len(pick_called) == 0  # picker not invoked
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "picker" -v
```

- [ ] **Step 3: Implement**

In `_name_search` (and `_contents_search` after collecting results):

```python
from cc_session_tools.lib.picker import pick_from_list

PICKER_MAX = 10

def _maybe_pick_and_resume(results: list[_Result], do_global: bool) -> int | None:
    """If ≤10 results and TTY, show picker and exec into ccr. Returns exit code
    or None if picker was not shown."""
    if len(results) > PICKER_MAX or not sys.stdin.isatty():
        return None
    labels = [
        f"{r.basename} ({_display_path(r.project_dir)})" if do_global else r.basename
        for r in results
    ]
    idx = pick_from_list(labels)
    if idx is None:
        return 0
    os.execvp("ccr", ["ccr", results[idx].basename])
    return 0  # unreachable but satisfies type checker
```

Call `_maybe_pick_and_resume` at the end of `_name_search` before the final return:

```python
pick_rc = _maybe_pick_and_resume(results, do_global)
if pick_rc is not None:
    return pick_rc
# else fall through to list display (non-TTY or >10 results)
for r in results:
    print(r.basename ...)
return 0
```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_cli_ccs.py
git commit -m "feat(ccs): interactive 1-9/0 picker for <=10 results, exec into ccr"
```

---

## Task 17: `ccr` — session picker integration

**Files:**
- Modify: `src/cc_session_tools/cli/ccr.py`
- Modify: `tests/test_cli_ccr.py`

When 2-10 matches, show picker and resume the selected one. >10: keep current "re-run" message.

- [ ] **Step 1: Write failing tests**

```python
def test_ccr_picker_shown_for_2_to_10_matches(fake_repos, captured_launch, monkeypatch):
    _make_session(fake_repos, "proj1", "20260504-foo-one")
    _make_session(fake_repos, "proj2", "20260503-foo-two")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    
    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda _: 0)  # pick first
    
    rc = ccr.main(["foo"])
    assert rc == 0
    assert "20260504-foo-one" in captured_launch["cmd"]


def test_ccr_keeps_rerrun_message_for_more_than_10(fake_repos, monkeypatch, capsys):
    for i in range(11):
        _make_session(fake_repos, f"proj{i}", f"2026050{1}-foo-{i:02d}")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    
    rc = ccr.main(["foo"])
    assert rc == 0  # exits 0 with message
    out = capsys.readouterr().out
    assert "Multiple sessions" in out
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccr.py -k "picker" -v
```

- [ ] **Step 3: Implement**

Replace the existing `len(matches) > 1` branch in `main()`:

```python
if len(matches) > 1:
    if len(matches) <= 10 and sys.stdin.isatty():
        from cc_session_tools.lib.picker import pick_from_list
        labels = [f"{m.basename} ({m.project_dir})" for m in matches]
        idx = pick_from_list(labels)
        if idx is None:
            return 0
        m = matches[idx]
        # fall through to single-match resume logic
    else:
        print("Multiple sessions match that name tag fragment:")
        for m in matches:
            print(f"  {m.basename} ({m.project_dir})")
        print("Please re-run ccr with an unambiguous fragment ...")
        return 0
```

- [ ] **Step 4: Run tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccr.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccr.py tests/test_cli_ccr.py
git commit -m "feat(ccr): interactive 1-9/0 picker for 2-10 matching sessions"
```

---

## Task 18: CCX_DEBUG `--debug` flag for ccs, ccr, ccd

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `src/cc_session_tools/cli/ccr.py`
- Modify: `src/cc_session_tools/cli/ccd.py`
- Modify: `tests/test_cli_ccs.py`, `tests/test_cli_ccr.py`, `tests/test_cli_ccd.py`

Uses `lib/debug.py` from Task 1.

- [ ] **Step 1: Write failing tests for ccs**

```python
def test_ccs_debug_flag_sets_env(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo")
    monkeypatch.chdir(proj)
    monkeypatch.delenv("CCX_DEBUG", raising=False)
    
    ccs.main(["foo", "--debug"])
    err = capsys.readouterr().err
    assert "[CCX_DEBUG]" in err


def test_ccs_debug_env_var_also_works(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo")
    monkeypatch.chdir(proj)
    monkeypatch.setenv("CCX_DEBUG", "1")
    
    ccs.main(["foo"])
    err = capsys.readouterr().err
    assert "[CCX_DEBUG]" in err
```

- [ ] **Step 2: Run tests, confirm FAIL**

```bash
uv run pytest tests/test_cli_ccs.py -k "debug" -v
```

- [ ] **Step 3: Implement for all three CLIs**

Each CLI:
1. Add `--debug` flag to parser: `p.add_argument("--debug", action="store_true", help="Enable debug output (also: CCX_DEBUG=1).")`
2. In `main()`, if `args.debug`: `os.environ["CCX_DEBUG"] = "1"` (set before any `debug()` calls)
3. Add `debug()` calls in key places:

**ccs:** after computing `pairs` and `sessions`:
```python
from cc_session_tools.lib.debug import debug
debug(f"roots: {[str(p) for _, p in pairs]}")
debug(f"sessions found: {len(sessions)}")
```
And when running rg: `debug(f"cmd: {' '.join(cmd)}")`

**ccr:** after computing matches:
```python
from cc_session_tools.lib.debug import debug
debug(f"roots: {roots}")
debug(f"matches: {[m.basename for m in matches]}")
```
Before exec: `debug(f"resuming: {m.basename} in {m.project_dir}")`

**ccd:** check `ccd.py` structure; add debug for tag and session_dir being set.

- [ ] **Step 4: Run all tests, confirm PASS**

```bash
uv run pytest tests/test_cli_ccs.py tests/test_cli_ccr.py tests/test_cli_ccd.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py src/cc_session_tools/cli/ccr.py src/cc_session_tools/cli/ccd.py tests/
git commit -m "feat: add --debug flag and CCX_DEBUG env var to ccs, ccr, ccd"
```

---

## Task 19: Version bump to `0.7.0`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Run full test suite to verify everything passes**

```bash
uv run pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 2: Update version**

In `pyproject.toml`, change:
```
version = "0.5.0"
```
to:
```
version = "0.7.0"
```

- [ ] **Step 3: Verify version is reflected**

```bash
uv run ccs --version
```

Expected: `ccs 0.7.0`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.7.0"
```

---

## Task 20: Final check and spec commit

- [ ] **Step 1: Run full test suite one final time**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: all tests pass (existing + new).

- [ ] **Step 2: Commit spec and plan docs**

```bash
git add docs/superpowers/
git commit -m "docs: add ccx improvements spec and implementation plan"
```

- [ ] **Step 3: Push feature branch**

```bash
git push -u origin f/20260510-ccx-improvements
```

---

## File structure summary

| File | Action | Description |
|------|--------|-------------|
| `src/cc_session_tools/lib/debug.py` | Create | CCX_DEBUG env-var support |
| `src/cc_session_tools/lib/picker.py` | Create | Shared 1-9/0 interactive picker |
| `src/cc_session_tools/lib/claude_flags.py` | Create | Runtime claude flag enumeration + cache |
| `src/cc_session_tools/lib/sessions.py` | Modify | Add `transcript_dir_for_project` |
| `src/cc_session_tools/cli/ccs.py` | Modify | 10 improvements (hooks, dates, JSON, global, suggestions, OSC8, transcripts, batched ETA, picker, debug) |
| `src/cc_session_tools/cli/ccr.py` | Modify | 5 improvements (exact-match, PATH check, flag pass-through, picker, debug) |
| `src/cc_session_tools/cli/ccd.py` | Modify | Add debug support |
| `pyproject.toml` | Modify | Version → 0.7.0 |
| `tests/test_debug.py` | Create | Debug module tests |
| `tests/test_picker.py` | Create | Picker tests |
| `tests/test_claude_flags.py` | Create | Claude flags enumeration tests |
| `tests/test_ccs_eta.py` | Create | ETA formula and batch sizing tests |
| `tests/test_sessions.py` | Modify | Add transcript_dir encoding tests |
| `tests/test_cli_ccs.py` | Modify | All new ccs feature tests |
| `tests/test_cli_ccr.py` | Modify | All new ccr feature tests |
| `tests/test_cli_ccd.py` | Modify | Debug flag tests |
