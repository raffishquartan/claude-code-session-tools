# CCX Improvements Design

**Date:** 2026-05-10
**Scope:** ccs, ccr, ccd CLI tools in `cc-session-tools`
**Target version:** `0.7.0` (current `0.5.0`, increment by 2 to reserve space for a parallel branch)

---

## 1. Background

`ccs` searches Claude Code sessions by name or content. `ccr` resumes sessions by tag fragment. `ccd` starts new sessions. The tools are used daily across many projects.

This spec covers fourteen improvements across three categories:

- **UX friction** (interactive picker, ETA accuracy, OSC 8 links, zero-result suggestions)
- **Search power** (date filters, transcript search, hook filtering, global default, JSON output)
- **Robustness** (exact-match fast-path, claude flag pass-through, debug mode, PATH check)

---

## 2. Feature Specifications

### 2.1 Iterative ETA for `ccs --contents` (batched rg)

**Problem:** the current `rg` path issues one big invocation across all sessions in parallel (`rg ... [all-dirs]`). The upfront estimate `sample_time × N` assumes sequential processing and can be 10-50× too high.

**Solution:** three sequential batched rg invocations. Each batch completes, then the cumulative elapsed time feeds the iterative formula before the next batch starts.

**Batch sizing:**

| Total sessions Z | Batch 1 | Batch 2    | Batch 3      |
|-----------------|---------|------------|--------------|
| Z ≤ 10          | all Z   | —          | —            |
| 10 < Z ≤ 110    | 10      | Z−10       | —            |
| Z > 110         | 10      | 100        | Z−110        |

**ETA formula** (printed after each batch completes, before starting next):

```
X = total elapsed so far (seconds)
Y = sessions processed so far
Z = total sessions
total_est = X + (X / Y) * (Z - Y)
```

Output line: `Batch K/M done (Y/Z sessions, Xs elapsed). Est total: ~Ys`

**Results merging:** each batch streams `rg` output into a shared accumulator. After all batches, the accumulated lines are grouped and displayed as now.

**Fallback (grep path):** the grep path already processes per-session in a thread pool. Update its progress line to show estimated *total* time (`X + (X/Y)*(Z-Y)`) rather than remaining time (`(X/Y)*(Z-Y)`) only.

**Edge cases:**
- If a batch produces no rg output (returncode=1): still update elapsed/completed, continue.
- If rg fails (returncode>1): fall back to grep as now.
- Z=0 before batching: handled by existing "no sessions" guard.

---

### 2.2 Session picker for `ccs`

**Trigger:** name-search or `--contents` returns 1–10 sessions AND `sys.stdin.isatty()`.

**Display format (to stdout):**

```
  1) 20260510-improve-ccx  (~/repos/claude-code-session-tools)
  2) 20260509-fix-prefix-prompt
  ...
  9) ...
  0) ...  (only if exactly 10 results)
Pick [1-9, q to cancel]:
```

Items are numbered 1–9, then 0 for the 10th (so "6" picks the 6th session).
In `--global` mode the project dir is shown in parentheses; otherwise omitted.

**After pick:** `os.execvp('ccr', ['ccr', selected.basename])`. ccr handles the cd-to-project-dir, env-var setup, and `claude --resume`.

**If >10 results:** print list as now, no picker.
**If `--json` or `--null` active:** no picker (machine-readable output must be non-interactive).
**If not a TTY:** no picker, print list and exit as now.

---

### 2.3 Session picker for `ccr`

**Trigger:** `find_matching_sessions` returns 2–10 matches (replaces current "re-run with unambiguous fragment" message).

**Display format (to stdout):** same 1–9/0 numbered list as ccs (shared function).

**After pick:** call `launch_claude_resume` with the selected `SessionMatch` (same code path as the existing single-match branch).

**If >10 matches:** keep current "Multiple sessions match" + re-run message.
**If 0 matches:** keep current "no sessions match" error.
**If 1 match:** resume directly as now.

---

### 2.4 Shared picker: `lib/picker.py`

Single function used by both ccs and ccr:

```python
def pick_from_list(labels: list[str]) -> int | None:
    """Display a 1-9/0 numbered menu. Returns 0-based index or None if cancelled.

    Requires 1 ≤ len(labels) ≤ 10. Reads one line from stdin (digit + Enter).
    Returns None on EOF, KeyboardInterrupt, 'q', or out-of-range input.
    """
```

Numbering: positions 0..8 display as 1..9; position 9 displays as 0.
Input: `input()` - reads digit + Enter. No raw-terminal mode needed.
Non-TTY guard: callers check `sys.stdin.isatty()` before calling; `pick_from_list` itself does not check (callers own the guard).

---

### 2.5 `ccs --contents`: also search Claude transcript JSONL files

**Background:** the actual conversation lives in `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. Currently `ccs --contents` only searches files inside `cc-sessions/<tag>/`, missing the transcript content entirely.

**Encoding formula:**
```python
encoded_cwd = str(project_dir.resolve()).replace('/', '-').replace('.', '-')
transcript_dir = Path.home() / '.claude' / 'projects' / encoded_cwd
```

**Integration:** when building the rg/grep target list, add `transcript_dir` alongside each session's `cc-sessions/<tag>/` directory if it exists. The results grouping logic needs to map JSONL file paths back to their project's session display.

**New helper in `lib/sessions.py`:** `transcript_dir_for_project(project_dir: Path) -> Path` - returns the encoded path (does not check existence).

**Display:** transcript matches are shown under the session name with `[transcript]` prefix on the context lines, or just shown inline (caller decides).

---

### 2.6 `ccs --json` and `--null` output

Two new mutually-exclusive flags:

- `--json`: output a JSON array. Each element: `{"basename": str, "project_dir": str, "context_lines": [str]}`. context_lines is `[]` for name-search.
- `--null`: output null-delimited basenames (`<basename>\0<basename>\0...`). Suitable for `xargs -0 ccr`.

Both flags: skip progress output, skip picker, write to stdout, exit 0 even if no results (empty array `[]` / empty string).

Priority: `--null` > `--json` (if both supplied, `--null` wins).

---

### 2.7 `ccr` exact-match fast-path

Before calling `find_matching_sessions` (which enumerates all roots):

1. Check if `args.fragment` matches `SESSION_FULL_RE` exactly (i.e., looks like a full session basename).
2. If yes: search all roots for an exact basename match (string equality).
3. If found: use it directly (skip substring search entirely).
4. If not found: fall through to normal substring search.

This avoids root enumeration for the common case of pasting a full session name from `ccs` output.

---

### 2.8 `ccs --exclude-hooks` / `--no-hooks`

New flag: `--exclude-hooks` (long form; also accept `--no-hooks` as alias).

**Hook session detection:** a session is considered a hook-security session if its basename tag (the part after `YYYYMMDD-`) contains `hook` (case-insensitive). This matches the naming pattern of sessions created by the bash-hard-deny / hook-security hooks.

**Behaviour:** sessions matching this criterion are excluded from all output modes (name-search, --contents, --json, --null). A count of excluded sessions is printed to stderr when any are excluded: `ccs: excluded N hook-security session(s).`

**Help text:** `--exclude-hooks  Exclude hook-security-check sessions from results (sessions whose tag contains 'hook').`

---

### 2.9 `ccs` date-range filters

Three new flags (all apply to `session_start_date`):

- `--since YYYYMMDD`: include only sessions with start date ≥ this date.
- `--before YYYYMMDD`: include only sessions with start date < this date.
- `--days N`: include only sessions started within the last N days (inclusive of today). Equivalent to `--since <today - N days>`.

Applied as a pre-filter before grep/rg, reducing work. Validation: `--since`/`--before` values must be valid `YYYYMMDD` (8-digit, no dashes) dates; error and exit 1 if not. `--days` must be a positive integer. The `YYYYMMDD` format matches `session_start_date()` output, enabling direct string comparison.

---

### 2.10 `ccr` claude-flag pass-through

**Problem:** users can't pass extra flags to `claude --resume` (e.g., `--model sonnet`).

**Solution:** enumerate valid claude flags at runtime, accept only those.

**Implementation:**

1. `ccr` uses `argparse.parse_known_args` to separate its own flags from remainder.
2. On first call (or when claude binary mtime changes): run `claude --help`, parse the output to extract all long-form flags (lines matching `--<flag>`). Cache in `~/.cache/cc-session-tools/claude-flags.json` with the claude binary path and mtime.
3. Validate each remainder arg: if it starts with `--` and the flag name is in the cached set, allow it. If unknown: print `ccr: unknown flag '--foo'; not a recognised claude option` and exit 1.
4. Append validated remainder args to the `claude --resume ...` command.

**Short flags** (single `-`): pass through without validation (too many edge cases with combined single-char flags).

---

### 2.11 `CCX_DEBUG=1` / `--debug` for ccs, ccr, ccd

New env var: `CCX_DEBUG=1`. Also available as `--debug` CLI flag on each tool.

When active, print to stderr before the main operation:

- `[CCX_DEBUG] roots: [<list>]`
- `[CCX_DEBUG] scope: global | cwd=<path>`
- `[CCX_DEBUG] sessions found: N`
- `[CCX_DEBUG] cmd: <exact rg or grep command>` (ccs only)
- `[CCX_DEBUG] resuming: <basename> in <project_dir>` (ccr only)
- `[CCX_DEBUG] tag: <tag> session_dir: <path>` (ccd only)

---

### 2.12 `CCS_DEFAULT_GLOBAL=1`

If env var `CCS_DEFAULT_GLOBAL=1` is set: `--global` defaults to `True` for `ccs`. Add `--local` flag to override back to cwd-only scope.

Help text when `CCS_DEFAULT_GLOBAL` is detected: `(CCS_DEFAULT_GLOBAL=1 is set; --local overrides to cwd-only scope)`

---

### 2.13 `ccr`: clear error if claude not on PATH

Before `os.execvpe`:

```python
if not shutil.which("claude"):
    print("ccr: 'claude' not found on PATH - is Claude Code installed?", file=sys.stderr)
    return 1
```

---

### 2.14 `ccs` OSC 8 terminal hyperlinks

When `sys.stdout.isatty()` and `os.environ.get('NO_COLOR') is None` and `os.environ.get('TERM') != 'dumb'`:

Wrap session basenames in OSC 8 escape sequences pointing to the session directory:

```python
def _osc8_link(text: str, path: Path) -> str:
    uri = path.as_uri()  # file:///...
    return f"\033]8;;{uri}\033\\{text}\033]8;;\033\\"
```

Applied to the `basename` portion of each result line. Degrades silently in non-supporting terminals (escape codes are invisible; some may show garbage - guard with TTY + NO_COLOR checks mitigates this).

---

### 2.15 `ccs` "Did you mean?" on zero results

When name-search returns 0 results:

```python
import difflib
suggestions = difflib.get_close_matches(query, all_basenames, n=3, cutoff=0.4)
if suggestions:
    print(f"ccs: did you mean: {', '.join(suggestions)}?", file=sys.stderr)
```

Applied after the "no sessions match" message.

---

## 3. Shared library additions

| Module | Addition |
|--------|----------|
| `lib/picker.py` | `pick_from_list(labels) -> int \| None` |
| `lib/sessions.py` | `transcript_dir_for_project(project_dir) -> Path` |
| `lib/claude_flags.py` | `get_claude_flags() -> set[str]` (cached, runtime-enumerated) |
| `lib/debug.py` | `debug_print(*args)` (honours `CCX_DEBUG` / `--debug`) |

---

## 4. Testing

All new behaviour must have unit/integration tests in `tests/`. Specifically:

- `tests/test_picker.py`: pick_from_list with various inputs, cancellation, out-of-range
- `tests/test_ccs_*.py`: date filters, hook exclusion, JSON/null output, OSC 8 formatting, zero-result suggestions
- `tests/test_ccr.py`: exact-match fast-path, picker (mocked launch), PATH check, flag pass-through validation
- `tests/test_sessions.py`: `transcript_dir_for_project` encoding
- `tests/test_eta.py`: batched ETA formula correctness, batch sizing

Existing tests must continue passing. `launch_claude_resume` remains monkeypatchable.

---

## 5. Version

Bump `pyproject.toml` `version` from `0.5.0` to `0.7.0`.
