# Context-window-warning + context-override: CCCS → CCST Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reimplement the CCCS-owned `context-window-warning.sh` Stop hook and `context-override`
skill natively inside CCST, with identical behaviour, then ship as a version bump (see Task 12 —
number pending your answer to the spec's §9 open question).

**Architecture:** A new `ccst hooks run context-window-warning` verb
(`src/cccs_hooks/context_window_warning.py`) reads the same two thresholds off the transcript,
using a literally-ported `src/cccs_hooks/model_info.py` lookup table and a local token-counting
function (not `cccs_hooks.transcript`, which doesn't carry usage data). The override flag moves from
a `~/.claude/context-overrides/<session_id>` file into a new `context_overrides` table in the shared
`sessions.db`, behind a new `src/cc_session_tools/lib/context_overrides.py` and a new top-level `ccst
context-override [on|off|status]` subcommand, which also replaces the skill's standalone bash
script. The ported skill lives at `src/cc_session_tools/skills/context-override/SKILL.md`.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`/`json`/`math`/`pathlib`, `pytest`, `pytest-mock`.

**Spec:** `docs/superpowers/specs/2026-08-17-context-window-warning-to-ccst-design.md`. The spec is
authoritative. Section references below (§1-§9) point into it.

---

## Diagnosis / context for the engineer (already established — not open questions)

1. **CCST's hook framework is `HOOK_VERBS` (`lib/hook_registry.py`) → `config/hooks-bundle.json` →
   `ccst hooks install`/`install-everything --apply` merges into `settings.json`.**
   `hooks_install.merge_hook_settings` only adds bundle entries; `prune_stale_hooks` only removes
   entries whose command starts with `"ccst hooks run "`. Foreign entries (CCCS's raw-path Stop hook)
   are never touched either way.
2. **`~/.claude/skills/context-override` is today a real directory** (`drwx------`, CCCS's own
   sync target), not a symlink. `ccst skills install` refuses (rc 1, no write) when the destination
   is a real dir and `--force` is absent, and auto-apply never passes `--force`. The ship task must
   `rm -rf` it first (§7, Task 11).
3. **`claude_code_usage.pricing` is deliberately not reused for the price figure** — no
   `context_window` field, and its rates are precise litellm figures, not this hook's flat
   "cache-read ≈ 0.1× standard input" approximation. Reusing it would be a real behaviour change.
   `model_info.py` ports the bash case tables verbatim instead (§2).
4. **bc `scale=2` truncates; Python `.2f` rounds.** The cost formula must floor-then-format, not
   format-and-round, or costs silently drift from the bash version on values right at a rounding
   boundary. Golden test case: `tokens=155000, price=3.00` → `"0.04"` (§4) — this is the exact figure
   from the warning pasted at the top of this session, so it doubles as a regression anchor against
   the live system's own prior output.
5. **`sessions.db`'s tables are created via `CREATE TABLE IF NOT EXISTS` inside `connect()`**, the
   same additive-DDL pattern `doctor_mutes`/`install_sync` already use — no migration step is needed
   for the new `context_overrides` table, and no `ccst doctor` FAIL-until-migrated check is owed
   (nothing existing becomes unreadable).
6. **The version bump is an open question, not a default.** Task 12 is a placeholder pending your
   answer to spec §9 (2.5.1 as you asked, vs 2.6.0 per this repo's own documented policy for
   "new... CLI subcommand"). Do not pick one and proceed past Task 12 without that answer.

---

## File structure

| File | Change |
|---|---|
| `src/cccs_hooks/model_info.py` | **New.** `context_window`, `input_price_per_mtok`, `display_name` (Task 1) |
| `src/cccs_hooks/context_window_warning.py` | **New.** `_current_context_tokens`, arithmetic helpers, `main` (Tasks 2-4) |
| `src/cc_session_tools/lib/hook_registry.py` | Add `context-window-warning` to `HOOK_VERBS` + `HOOK_DESCRIPTIONS` (Task 5) |
| `src/cc_session_tools/config/hooks-bundle.json` | Add entry to the `Stop` block (Task 5) |
| `src/cc_session_tools/lib/context_overrides.py` | **New.** `get_override`, `set_override` (Task 6) |
| `src/cc_session_tools/lib/sessions_db.py` | `context_overrides` table DDL (Task 6) |
| `src/cc_session_tools/cli/ccst.py` | `context-override` subparser + `_cmd_context_override`; module docstring update (Task 7) |
| `src/cc_session_tools/skills/context-override/SKILL.md` | **New** (ported) (Task 8) |
| `src/cccs_hooks/context_window_warning.py` | Wire in the override check (Task 9, after Task 6 exists) |
| `tests/test_model_info.py` | **New** (Task 1) |
| `tests/test_context_window_warning.py` | **New** (Tasks 2-4, 9) |
| `tests/test_context_overrides.py` | **New** (Task 6) |
| `tests/test_ccst_context_override_cli.py` | **New** (Task 7) |
| `README.md`, `CHANGELOG.md` | Task 10 |
| `pyproject.toml` | Version bump (Task 12, number pending) |

---

### Task 0: Worktree setup

**Files:** none (environment only)

- [ ] **Step 1:**
  ```bash
  cd ~/repos/claude-code-session-tools && git fetch origin
  cd ~/repos/claude-code-session-tools && git worktree add .worktrees/context-window-warning-to-ccst -b f/20260817-context-window-warning-to-ccst origin/main
  ```
- [ ] **Step 2:**
  ```bash
  cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && uv sync --extra dev
  ```
- [ ] **Step 3:** Confirm a green baseline: `uv run pytest -q`. Fix any pre-existing failure before
  adding anything — you own the codebase while you're in it.

All subsequent paths are relative to the worktree root.

---

### Task 1: `cccs_hooks/model_info.py`

§2. Literal port of `hooks/lib/model-info.sh`'s three lookup tables.

**Files:** Create `src/cccs_hooks/model_info.py`; Create `tests/test_model_info.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_info.py
from cccs_hooks import model_info

CASES = [
    ("claude-fable-5", 1_000_000, 10.00, "Fable 5"),
    ("claude-mythos-5", 1_000_000, 10.00, "Mythos 5"),
    ("claude-opus-5", 1_000_000, 5.00, "Opus 5"),
    ("claude-opus-4-8", 1_000_000, 5.00, "Opus 4.8"),
    ("claude-opus-4-7", 1_000_000, 5.00, "Opus 4.7"),
    ("claude-opus-4-6", 1_000_000, 5.00, "Opus 4.6"),
    ("claude-sonnet-5", 1_000_000, 3.00, "Sonnet 5"),
    ("claude-sonnet-4-6", 1_000_000, 3.00, "Sonnet 4.6"),
    ("claude-haiku-4-5-20251001", 200_000, 1.00, "Haiku 4.5"),
]


def test_known_models():
    for model_id, window, price, name in CASES:
        assert model_info.context_window(model_id) == window
        assert model_info.input_price_per_mtok(model_id) == price
        assert model_info.display_name(model_id) == name


def test_unrecognized_model_falls_back():
    assert model_info.context_window("claude-nonexistent-9") == 200_000
    assert model_info.input_price_per_mtok("claude-nonexistent-9") == 5.00
    assert model_info.display_name("claude-nonexistent-9") == "an unrecognized model"


def test_empty_model_id_falls_back():
    assert model_info.context_window("") == 200_000
```

- [ ] **Step 2:** `uv run pytest tests/test_model_info.py -v` — expect `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/cccs_hooks/model_info.py
"""Model id -> context window size, standard input price, display name.

Ported verbatim from claude-code-config-sync's hooks/lib/model-info.sh (the
source of truth this table is migrating from). Consumed by
context_window_warning to phrase its nudge in terms of "% of the model's
actual window" and to estimate a per-model cache-read cost.

Figures current as of 2026-08-13 (Claude 5 family). Context windows: Fable 5 /
Mythos 5 / Opus 5 / Opus 4.6-4.8 / Sonnet 5 / Sonnet 4.6 all 1,000,000 tokens
(the default, not a beta long-context mode); Haiku 4.5 stays at 200,000.
Prices are the standard (non-cached) input rate per million tokens.

Re-check this table whenever a new model ships. A model not matched by any
case falls through to the default row (200000 tokens / $5.00 per MTok / "an
unrecognized model") - deliberately the same window as every pre-Claude-5
model, so an unrecognized model degrades to conservative,
already-correct-for-most-history behaviour rather than going quiet or firing
too early.

Deliberately not `claude_code_usage.pricing`: that table has no context-window
field and carries precise litellm-sourced rates for cost *accounting*, not
this module's flat "cache-read is ~0.1x standard input" nudge approximation.
Reusing it would drift the approximation onto a different number - see the
design spec's section 2.
"""
from __future__ import annotations

_WINDOW_1M = {
    "claude-fable-5", "claude-mythos-5",
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6",
}
_PRICE = {
    "claude-fable-5": 10.00, "claude-mythos-5": 10.00,
    "claude-opus-5": 5.00, "claude-opus-4-8": 5.00, "claude-opus-4-7": 5.00, "claude-opus-4-6": 5.00,
    "claude-sonnet-5": 3.00, "claude-sonnet-4-6": 3.00,
}
_NAME = {
    "claude-fable-5": "Fable 5", "claude-mythos-5": "Mythos 5",
    "claude-opus-5": "Opus 5", "claude-opus-4-8": "Opus 4.8",
    "claude-opus-4-7": "Opus 4.7", "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-5": "Sonnet 5", "claude-sonnet-4-6": "Sonnet 4.6",
}
_DEFAULT_WINDOW = 200_000
_DEFAULT_PRICE = 5.00
_DEFAULT_NAME = "an unrecognized model"


def _is_haiku_4_5(model_id: str) -> bool:
    return model_id.startswith("claude-haiku-4-5")


def context_window(model_id: str) -> int:
    if model_id in _WINDOW_1M:
        return 1_000_000
    if _is_haiku_4_5(model_id):
        return 200_000
    return _DEFAULT_WINDOW


def input_price_per_mtok(model_id: str) -> float:
    if model_id in _PRICE:
        return _PRICE[model_id]
    if _is_haiku_4_5(model_id):
        return 1.00
    return _DEFAULT_PRICE


def display_name(model_id: str) -> str:
    if model_id in _NAME:
        return _NAME[model_id]
    if _is_haiku_4_5(model_id):
        return "Haiku 4.5"
    return _DEFAULT_NAME
```

- [ ] **Step 4:** `uv run pytest tests/test_model_info.py -v` — expect PASS.

- [ ] **Step 5: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cccs_hooks/model_info.py tests/test_model_info.py && git commit -m "feat(hooks): add model_info, ported from CCCS's model-info.sh

Literal port of the context-window/price/display-name lookup tables that
back the forthcoming context-window-warning hook. Deliberately not
claude_code_usage.pricing - see the module docstring for why reusing it
would be a real behaviour change, not a refactor."
```

---

### Task 2: `context_window_warning._current_context_tokens`

§3. Local function, not `cccs_hooks.transcript` (which doesn't carry `usage`).

**Files:** Create `src/cccs_hooks/context_window_warning.py`; Create `tests/test_context_window_warning.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_context_window_warning.py
import json
from pathlib import Path

import pytest

from cccs_hooks import context_window_warning as cww


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_fresh_session_has_zero_tokens(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "user", "message": {"content": "hi"}}])
    assert cww._current_context_tokens(p) == (0, "")


def test_reads_last_assistant_usage(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 100, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 5000}}},
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 154800}}},
    ])
    assert cww._current_context_tokens(p) == (155000, "claude-sonnet-5")


def test_sidechain_turns_are_skipped(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
        {"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-4-5",
            "usage": {"input_tokens": 9, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    ])
    assert cww._current_context_tokens(p) == (100, "claude-sonnet-5")


def test_blank_lines_tolerated(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text('\n{"type": "user", "message": {"content": "hi"}}\n\n')
    assert cww._current_context_tokens(p) == (0, "")


def test_missing_transcript_raises(tmp_path: Path):
    with pytest.raises(cww.TranscriptError):
        cww._current_context_tokens(tmp_path / "nope.jsonl")


def test_corrupt_transcript_raises(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text("not json at all\nstill not json\n")
    with pytest.raises(cww.TranscriptError):
        cww._current_context_tokens(p)
```

- [ ] **Step 2:** `uv run pytest tests/test_context_window_warning.py -v` — expect `ModuleNotFoundError`.

- [ ] **Step 3: Implement** (`_current_context_tokens` + `TranscriptError` only — `main` comes in Task 4)

```python
# src/cccs_hooks/context_window_warning.py  (partial - grows in Tasks 3-4, 9)
"""Stop hook: nudge to run /compact when the context window grows large.

  >= 150k tokens (orange): visible nudge, work continues.
  >= 200k tokens (red): more prominent nudge, work continues.

Ported from claude-code-config-sync's hooks/stop/context-window-warning.sh -
see the design spec (docs/superpowers/specs/2026-08-17-...-design.md) for the
full rationale. The two thresholds are fixed absolute token counts,
deliberately NOT scaled to the active model's context window: cache-read cost
and compaction cost scale with absolute tokens, not with what fraction of a
200k or a 1M window that represents. What DOES depend on the model is the
message text (via model_info), so the same absolute trigger doesn't read as
"nearly out of room" on a large-window model when it isn't. Neither tier uses
alarm language - both are non-blocking nudges, and the message says so.

Escape hatch: if the user has run /context-override this session (a per-
session row in sessions.db's context_overrides table, keyed by session_id),
the hook exits silently - no warning at all.

Fires at most once per turn (guarded by stop_hook_active, so the continuation
it triggers does not re-fire). A fresh session with no usage stays silent. A
genuine transcript-read error is surfaced to the user on stderr, non-blocking
(exit 1), so a parsing problem is visible but never wedges the session.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from cccs_hooks import model_info

THRESHOLD_ORANGE = 150_000
THRESHOLD_RED = 200_000


class TranscriptError(Exception):
    """Raised when the transcript is missing, unreadable, or has content but
    not a single parseable JSON line."""


def _current_context_tokens(transcript_path: Path) -> tuple[int, str]:
    """(token count, model id) from the last non-sidechain assistant message's
    usage. (0, "") for a fresh session with no assistant usage yet - that is a
    legitimate state, not an error."""
    if not transcript_path.is_file():
        raise TranscriptError(f"transcript not found: {transcript_path}")

    saw_nonempty = False
    parsed = 0
    last_usage: dict | None = None
    last_model = ""
    try:
        with transcript_path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                saw_nonempty = True
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed += 1
                if obj.get("isSidechain") is True:
                    continue
                message = obj.get("message") or {}
                usage = message.get("usage")
                if isinstance(usage, dict):
                    last_usage = usage
                    last_model = message.get("model") or ""
    except OSError as exc:
        raise TranscriptError(f"cannot read transcript: {exc}") from exc

    if saw_nonempty and parsed == 0:
        raise TranscriptError("transcript present but no parseable JSON lines")

    if last_usage is None:
        return 0, ""

    tokens = (
        int(last_usage.get("input_tokens", 0) or 0)
        + int(last_usage.get("cache_creation_input_tokens", 0) or 0)
        + int(last_usage.get("cache_read_input_tokens", 0) or 0)
    )
    return tokens, last_model
```

- [ ] **Step 4:** `uv run pytest tests/test_context_window_warning.py -v` — expect PASS.

- [ ] **Step 5: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cccs_hooks/context_window_warning.py tests/test_context_window_warning.py && git commit -m "feat(hooks): add context_window_warning._current_context_tokens

Local port of context-tokens.sh's transcript-usage-summing logic, not
folded into cccs_hooks.transcript.Turn - that dataclass doesn't carry
usage and is shared by confirm_8digit/worklog_guard for an unrelated
need."
```

---

### Task 3: Arithmetic helpers — truncating cost, integer PCT/K

§4. The load-bearing correctness task: bc truncates, Python's `.2f` rounds.

**Files:** Modify `src/cccs_hooks/context_window_warning.py`; Modify `tests/test_context_window_warning.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_context_window_warning.py

def test_golden_cost_case_matches_the_pasted_warning():
    """tokens=155000, Sonnet 5 ($3.00/MTok) is the exact figure from the
    context-warning message pasted at the top of the session that started
    this migration: '~155k tokens ... ~$0.04/turn in cache reads'."""
    assert cww._format_cost(tokens=155_000, price_per_mtok=3.00) == "0.04"


def test_cost_truncates_not_rounds():
    """0.0465 -> bc scale=2 truncates to 0.04; a naive f'{x:.2f}' can round to
    0.05. This case is chosen to sit just past a rounding boundary."""
    # tokens=200_000, price=1.755 -> 0.2 * 1.755 * 0.1 = 0.0351 -> truncates to 0.03,
    # while round(0.0351, 2) also gives 0.04 under some float reprs - use a
    # value where truncation and rounding clearly disagree:
    # tokens=180_000, price=2.999 -> 0.18 * 2.999 * 0.1 = 0.053982 -> truncate "0.05"
    # round to 2dp of 0.053982 -> "0.05" too (not a disagreement) - use:
    # tokens=199_000, price=3.00 -> 0.199*3.00*0.1 = 0.0597 -> truncate "0.05", round "0.06"
    assert cww._format_cost(tokens=199_000, price_per_mtok=3.00) == "0.05"


def test_k_and_pct_integer_arithmetic():
    assert cww._k_tokens(155_000) == 155
    assert cww._k_tokens(155_499) == 155  # (155499+500)//1000 == 155
    assert cww._k_tokens(155_500) == 156  # (155500+500)//1000 == 156
    assert cww._pct_of_window(155_000, 1_000_000) == 16  # (15500000+500000)//1000000
    assert cww._pct_of_window(200_000, 200_000) == 100


def test_window_label():
    assert cww._window_label(1_000_000) == "1M"
    assert cww._window_label(200_000) == "200k"
```

- [ ] **Step 2:** `uv run pytest tests/test_context_window_warning.py -k "cost or k_and_pct or window_label" -v`
  — expect `AttributeError`.

- [ ] **Step 3: Implement** (append to `context_window_warning.py`)

```python
def _k_tokens(tokens: int) -> int:
    return (tokens + 500) // 1000


def _pct_of_window(tokens: int, window: int) -> int:
    return (tokens * 100 + window // 2) // window


def _window_label(window: int) -> str:
    if window >= 1_000_000:
        return f"{window // 1_000_000}M"
    return f"{(window + 500) // 1000}k"


def _format_cost(*, tokens: int, price_per_mtok: float) -> str:
    """Cache-read cost, ~0.1x the model's standard input price, truncated
    (not rounded) to 2dp - matches `bc`'s `scale=2` semantics from the bash
    original. f'{x:.2f}' rounds and would silently drift from it."""
    raw = tokens / 1_000_000 * price_per_mtok * 0.1
    truncated = math.floor(raw * 100) / 100
    return f"{truncated:.2f}"
```

- [ ] **Step 4:** `uv run pytest tests/test_context_window_warning.py -v` — expect PASS.

- [ ] **Step 5: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cccs_hooks/context_window_warning.py tests/test_context_window_warning.py && git commit -m "feat(hooks): add truncating cost/PCT/K arithmetic to context_window_warning

bc's scale=2 truncates toward zero; Python's :.2f rounds. Using floor()
before formatting keeps the ported cost figure bit-for-bit identical to
the bash version instead of silently drifting on values near a rounding
boundary. Golden case is the exact $0.04 figure from the live warning
that prompted this migration."
```

---

### Task 4: `main()` — the hook entry point (override check deferred to Task 9)

§1. Wires the pieces together; loop guard, threshold bands, JSON decision output. The override
check is a stub returning `False` until Task 6/9 land the real store — **do not skip straight to
Task 9's version**, this task's tests pin down the threshold/message/loop-guard behaviour
independently of the override mechanism.

**Files:** Modify `src/cccs_hooks/context_window_warning.py`; Modify `tests/test_context_window_warning.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_context_window_warning.py
import io


def _run_main(monkeypatch, capsys, stdin_obj, transcript_lines=None, transcript_path=None):
    if transcript_path is None and transcript_lines is not None:
        transcript_path = "will not be reached"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_obj)))
    rc = cww.main([])
    return rc, capsys.readouterr()


def test_no_transcript_path_is_silent(monkeypatch, capsys):
    rc, out = _run_main(monkeypatch, capsys, {})
    assert rc == 0
    assert out.out == ""


def test_stop_hook_active_is_silent(monkeypatch, capsys):
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": "/x", "stop_hook_active": True})
    assert rc == 0
    assert out.out == ""


def test_below_orange_threshold_is_silent(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    assert rc == 0
    assert out.out == ""


def test_orange_band_emits_block_decision(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 155000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    assert rc == 0
    payload = json.loads(out.out)
    assert payload["decision"] == "block"
    assert "🟠 CONTEXT" in payload["reason"]
    assert "~155k tokens" in payload["reason"]
    assert "~$0.04/turn" in payload["reason"]


def test_red_band_emits_red_decision(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 200000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    payload = json.loads(out.out)
    assert "🔴 CONTEXT" in payload["reason"]


def test_override_silences_even_above_threshold(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 200000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: True)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    assert rc == 0
    assert out.out == ""


def test_unreadable_transcript_surfaces_error_nonblocking(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(tmp_path / "nope.jsonl"), "session_id": "s1"})
    assert rc == 1
    assert out.out == ""
    assert "context-window-warning" in out.err
```

- [ ] **Step 2:** `uv run pytest tests/test_context_window_warning.py -k "test_no_transcript or stop_hook_active or threshold or band or override_silences or unreadable" -v`
  — expect `AttributeError: module … has no attribute 'main'`.

- [ ] **Step 3: Implement** (append to `context_window_warning.py`)

```python
import datetime


def _is_overridden(session_id: str) -> bool:
    """Stubbed until Task 6 lands lib.context_overrides; always False so
    every threshold/message test in this task is independent of the store."""
    return False


def _reason(*, tokens: int, window: int, price: float, name: str, now: str, red: bool) -> str:
    k = _k_tokens(tokens)
    pct = _pct_of_window(tokens, window)
    label = _window_label(window)
    cost = _format_cost(tokens=tokens, price_per_mtok=price)
    emoji = "🔴" if red else "🟠"
    when = "when you get a natural break" if red else "whenever convenient"
    return (
        f"The context window has passed {k}k tokens - about {pct}% of {name}'s "
        f"{label}-token window. THIS IS A WARNING ONLY - output the two lines below at the "
        f"start of your response, then end your turn as you normally would. Do not restart "
        f"or repeat any work.\n"
        f"'{emoji} CONTEXT ({now}): ~{k}k tokens - ~{pct}% of the {label}-token window "
        f"({name}, ~${cost}/turn in cache reads). This is a nudge, not a limit - keep going; "
        f"/compact {when}.'\n"
        f"'(To silence this warning for the rest of the session, run /context-override.)'"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        stdin_obj = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        stdin_obj = {}

    if stdin_obj.get("stop_hook_active"):
        return 0

    transcript_path_str = stdin_obj.get("transcript_path") or ""
    if not transcript_path_str:
        return 0

    session_id = stdin_obj.get("session_id") or Path(transcript_path_str).stem

    try:
        tokens, model_id = _current_context_tokens(Path(transcript_path_str))
    except TranscriptError as exc:
        print(f"context-window-warning: {exc}", file=sys.stderr)
        return 1

    if tokens < THRESHOLD_ORANGE:
        return 0
    if _is_overridden(session_id):
        return 0

    window = model_info.context_window(model_id)
    price = model_info.input_price_per_mtok(model_id)
    name = model_info.display_name(model_id)
    now = datetime.datetime.now().strftime("%H:%M")
    red = tokens >= THRESHOLD_RED

    reason = _reason(tokens=tokens, window=window, price=price, name=name, now=now, red=red)
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0
```

- [ ] **Step 4:** `uv run pytest tests/test_context_window_warning.py -v` — expect PASS (whole file).

- [ ] **Step 5: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cccs_hooks/context_window_warning.py tests/test_context_window_warning.py && git commit -m "feat(hooks): add context_window_warning.main - the Stop hook entry point

Loop guard, no-transcript silence, threshold bands, and the block-decision
JSON output, ported behaviourally from CCCS's context-window-warning.sh.
_is_overridden is stubbed (always False) so this task's threshold/message
tests are independent of the sessions.db store landing in the next task."
```

---

### Task 5: Register the hook — `HOOK_VERBS` + bundle entry

§1. Now `ccst hooks run context-window-warning` dispatches.

**Files:** Modify `src/cc_session_tools/lib/hook_registry.py`; Modify `src/cc_session_tools/config/hooks-bundle.json`

- [ ] **Step 1:** In `hook_registry.py`, add to `HOOK_VERBS`:
  `"context-window-warning": "cccs_hooks.context_window_warning",`
  and to `HOOK_DESCRIPTIONS`:
  `"context-window-warning": "Nudges toward /compact when the context window passes 150k/200k tokens; silenced for the session by /context-override (Stop)",`

- [ ] **Step 2:** In `config/hooks-bundle.json`'s `"Stop"` block, insert as the **first** entry in the
  `hooks` array (ahead of `after-response`):
  ```json
  {
    "type": "command",
    "command": "ccst hooks run context-window-warning",
    "timeout": 5,
    "statusMessage": "Checking context-window size..."
  }
  ```

- [ ] **Step 3:** If a test asserts every bundle command name is present in `HOOK_VERBS` (check
  `tests/test_hooks_install.py` / `tests/test_hook_registry.py`), it should now pass without
  modification — that is the acceptance criterion for this task. If no such test exists, add one:
  ```python
  def test_context_window_warning_is_registered():
      from cc_session_tools.lib.hook_registry import HOOK_VERBS
      assert HOOK_VERBS["context-window-warning"] == "cccs_hooks.context_window_warning"
  ```

- [ ] **Step 4:** `uv run pytest -q` — expect PASS. Also smoke-test dispatch:
  ```bash
  echo '{}' | uv run python -m cc_session_tools.cli.ccst hooks run context-window-warning; echo "rc=$?"
  ```
  Expected: `rc=0`, no output (empty stdin → no `transcript_path`).

- [ ] **Step 5: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cc_session_tools/lib/hook_registry.py src/cc_session_tools/config/hooks-bundle.json tests/ && git commit -m "feat(hooks): register context-window-warning as a dispatchable ccst hook

Added to HOOK_VERBS/HOOK_DESCRIPTIONS and to the Stop block of the
default bundle, inserted ahead of after-response to preserve today's
effective firing order."
```

---

### Task 6: `context_overrides` table + `lib/context_overrides.py`

§5. Replaces the `~/.claude/context-overrides/<session_id>` flag file with a `sessions.db` table,
mirroring `doctor_mutes`'s shape and degrade-to-safe-default policy.

**Files:** Modify `src/cc_session_tools/lib/sessions_db.py`; Create `src/cc_session_tools/lib/context_overrides.py`; Create `tests/test_context_overrides.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_context_overrides.py
import sqlite3
from pathlib import Path

from cc_session_tools.lib import context_overrides, sessions_db


def test_get_override_defaults_false_when_never_set(tmp_path: Path):
    db = tmp_path / "sessions.db"
    assert context_overrides.get_override("s1", path=db) is False


def test_set_then_get_on(tmp_path: Path):
    db = tmp_path / "sessions.db"
    context_overrides.set_override("s1", "on", path=db)
    assert context_overrides.get_override("s1", path=db) is True


def test_set_then_get_off(tmp_path: Path):
    db = tmp_path / "sessions.db"
    context_overrides.set_override("s1", "on", path=db)
    context_overrides.set_override("s1", "off", path=db)
    assert context_overrides.get_override("s1", path=db) is False


def test_override_is_per_session(tmp_path: Path):
    db = tmp_path / "sessions.db"
    context_overrides.set_override("s1", "on", path=db)
    assert context_overrides.get_override("s2", path=db) is False


def test_get_override_on_missing_db_returns_false(tmp_path: Path):
    assert context_overrides.get_override("s1", path=tmp_path / "nope.db") is False


def test_get_override_on_corrupt_db_returns_false(tmp_path: Path):
    db = tmp_path / "sessions.db"
    db.write_bytes(b"not a sqlite file")
    assert context_overrides.get_override("s1", path=db) is False


def test_set_override_overwrites(tmp_path: Path):
    db = tmp_path / "sessions.db"
    context_overrides.set_override("s1", "on", path=db)
    context_overrides.set_override("s1", "on", path=db)  # idempotent, no UNIQUE-constraint error
    assert context_overrides.get_override("s1", path=db) is True
```

- [ ] **Step 2:** `uv run pytest tests/test_context_overrides.py -v` — expect `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Add to `sessions_db.py`'s schema-creation SQL (wherever the other `CREATE TABLE IF NOT EXISTS`
statements live, alongside `doctor_mutes`/`install_sync`):

```sql
CREATE TABLE IF NOT EXISTS context_overrides (
    session_id TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Create `src/cc_session_tools/lib/context_overrides.py`:

```python
"""Per-session /context-override flag: sessions.db table, not a flag file.

Ported from claude-code-config-sync's ~/.claude/context-overrides/<session_id>
flag file. Backed by the shared sessions.db (context_overrides table),
following doctor_mutes's established shape and degrade-to-safe-default
policy: context_window_warning fires on every Stop event in every open
session, so a read here must never be the thing that crashes someone's turn.
"on" means warnings are silenced for that session; the default (never set, or
any store failure) is False - warnings stay on, matching the bash version's
"fresh session starts with warnings active" behaviour.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cc_session_tools.lib import sessions_db


def get_override(session_id: str, *, path: Path | None = None) -> bool:
    try:
        conn = sessions_db.connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return False
    try:
        row = conn.execute(
            "SELECT state FROM context_overrides WHERE session_id = ?", (session_id,)
        ).fetchone()
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()
    return row is not None and row["state"] == "on"


def set_override(session_id: str, state: str, *, path: Path | None = None) -> None:
    conn = sessions_db.connect(path=path)
    try:
        conn.execute(
            "INSERT INTO context_overrides (session_id, state, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (session_id, state, sessions_db._now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4:** `uv run pytest tests/test_context_overrides.py -v && uv run pytest -q` — expect PASS.

- [ ] **Step 5: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cc_session_tools/lib/sessions_db.py src/cc_session_tools/lib/context_overrides.py tests/test_context_overrides.py && git commit -m "feat(context-override): add context_overrides sessions.db table + lib

Replaces the ~/.claude/context-overrides/<session_id> flag file with a
row in the shared sessions.db, matching this repo's data-store
conventions and doctor_mutes's degrade-to-safe-default pattern. No
migration step: the table is additive, created via the same
CREATE-TABLE-IF-NOT-EXISTS every other sessions.db table uses."
```

---

### Task 7: `ccst context-override [on|off|status]` CLI subcommand

§6. Replaces the skill's standalone bash script.

**Files:** Modify `src/cc_session_tools/cli/ccst.py`; Create `tests/test_ccst_context_override_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ccst_context_override_cli.py
import subprocess
import sys
from pathlib import Path


def _run(args, env, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "context-override", *args],
        capture_output=True, text=True, env=env, cwd=cwd,
    )


def test_on_default_action(monkeypatch, tmp_path, base_env):
    env = {**base_env, "CLAUDE_CODE_SESSION_ID": "s1", "CCST_SESSIONS_DIR": str(tmp_path)}
    result = _run([], env)
    assert result.returncode == 0
    assert "ON" in result.stdout


def test_off_action(base_env, tmp_path):
    env = {**base_env, "CLAUDE_CODE_SESSION_ID": "s1", "CCST_SESSIONS_DIR": str(tmp_path)}
    _run(["on"], env)
    result = _run(["off"], env)
    assert result.returncode == 0
    assert "OFF" in result.stdout


def test_status_action(base_env, tmp_path):
    env = {**base_env, "CLAUDE_CODE_SESSION_ID": "s1", "CCST_SESSIONS_DIR": str(tmp_path)}
    result = _run(["status"], env)
    assert result.returncode == 0
    assert "OFF" in result.stdout  # default, nothing set yet


def test_missing_session_id_errors(base_env, tmp_path):
    env = {**base_env, "CCST_SESSIONS_DIR": str(tmp_path)}
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    result = _run(["on"], env)
    assert result.returncode == 1
    assert "CLAUDE_CODE_SESSION_ID" in result.stderr


def test_unknown_action_errors(base_env, tmp_path):
    env = {**base_env, "CLAUDE_CODE_SESSION_ID": "s1", "CCST_SESSIONS_DIR": str(tmp_path)}
    result = _run(["bogus"], env)
    assert result.returncode != 0
```

Check `tests/conftest.py` for an existing `base_env`-shaped fixture (several `test_ccst_*.py` files
already shell out this way — reuse whatever fixture they use for `env=` rather than inventing a
second one; adjust the test bodies above to match its actual name/shape).

- [ ] **Step 2:** `uv run pytest tests/test_ccst_context_override_cli.py -v` — expect argparse
  failure (`invalid choice: 'context-override'`).

- [ ] **Step 3: Implement.** Add a subparser (bare noun + optional positional `action`, default
  `"on"` — same shape as `install-everything`'s bare-noun-plus-flag pattern) and its handler:

```python
def _cmd_context_override(args: argparse.Namespace) -> int:
    """ccst context-override [on|off|status] - the CLI half of the /context-override
    skill. Reads CLAUDE_CODE_SESSION_ID exactly as the ported bash script did."""
    from cc_session_tools.lib import context_overrides

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        print(
            "context-override: CLAUDE_CODE_SESSION_ID is not set; cannot key the flag to this session.",
            file=sys.stderr,
        )
        return 1

    action = args.action
    if action == "on":
        context_overrides.set_override(session_id, "on")
        print("Context-window override ON for this session.")
        print("The 100k/150k warnings are silenced until you run /context-override off or the session ends.")
        return 0
    if action == "off":
        context_overrides.set_override(session_id, "off")
        print("Context-window override OFF. The 100k/150k warnings will appear again.")
        return 0
    if action == "status":
        state = "ON" if context_overrides.get_override(session_id) else "OFF"
        print(f"Context-window override is {state} for this session.")
        return 0

    print(f"context-override: unknown action {action!r} (expected on|off|status).", file=sys.stderr)
    return 1
```

Register the subparser (near the other bare-noun subparsers, e.g. next to `install-everything`'s):

```python
p_context_override = subparsers.add_parser("context-override", help="Toggle the /context-override flag for the current session.")
p_context_override.add_argument("action", nargs="?", default="on", choices=["on", "off", "status"])
p_context_override.set_defaults(func=_cmd_context_override)
```

(Match whatever dispatch convention the surrounding subparsers actually use — `set_defaults(func=…)`
vs. a central `if args.noun == …` dispatch table — by reading the existing `install-everything`
subparser wiring immediately before this edit; the skeleton above assumes the former.)

Also append to the module docstring's "Current subcommands" list:
```
  context-override [on|off|status]  Toggle the /context-override flag for the current
                                 session (default: on). See ccst hooks run
                                 context-window-warning.
```

- [ ] **Step 4:** `uv run pytest tests/test_ccst_context_override_cli.py -v && uv run pytest -q` —
  expect PASS.

- [ ] **Step 5: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cc_session_tools/cli/ccst.py tests/test_ccst_context_override_cli.py && git commit -m "feat(cli): add ccst context-override [on|off|status]

Replaces the context-override skill's standalone bash script - this IS
the mechanism now, not a wrapper around a separate one. Satisfies
.claude/CLAUDE.md's data-store convention that a new store ship a
corresponding ccst query subcommand."
```

---

### Task 8: Wire the real override check into the hook

§1/§5. Task 4 stubbed `_is_overridden`; now it calls the real store.

**Files:** Modify `src/cccs_hooks/context_window_warning.py`; Modify `tests/test_context_window_warning.py`

- [ ] **Step 1:** Replace the stub:

```python
def _is_overridden(session_id: str) -> bool:
    from cc_session_tools.lib import context_overrides

    return context_overrides.get_override(session_id)
```

- [ ] **Step 2:** Add an integration test exercising the real store (not the monkeypatched stub):

```python
def test_override_end_to_end_via_real_store(tmp_path, monkeypatch, capsys):
    from cc_session_tools.lib import context_overrides

    db = tmp_path / "sessions.db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    context_overrides.set_override("s1", "on", path=db)

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 200000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": str(p), "session_id": "s1"})))
    rc = cww.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out == ""
```

Adjust the `path=db` vs. `CCST_SESSIONS_DIR` wiring to match however `sessions_db.default_db_path()`
actually resolves the directory (check `sessions_db.py:69` `default_db_path()` before assuming the
env var name) — the point of this test is that `main()` reaches the *real* `context_overrides`
module with no path override, so the env var (or whatever `default_db_path` honours) is what must
redirect it into `tmp_path`.

- [ ] **Step 3:** `uv run pytest tests/test_context_window_warning.py -v && uv run pytest -q` —
  expect PASS.

- [ ] **Step 4: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cccs_hooks/context_window_warning.py tests/test_context_window_warning.py && git commit -m "feat(hooks): wire the real context_overrides store into the Stop hook

Task 4 stubbed _is_overridden so the threshold/message tests didn't
depend on the store landing first. Now that lib.context_overrides and
ccst context-override both exist, the hook reads the real per-session
flag."
```

---

### Task 9: Port the `context-override` skill

§6. `SKILL.md` only — no `scripts/` directory, since the CLI subcommand from Task 7 replaces the
standalone script entirely.

**Files:** Create `src/cc_session_tools/skills/context-override/SKILL.md`

- [ ] **Step 1:** Create the file, porting `claude-code-config-sync/skills/context-override/SKILL.md`
  with the same `name`/`description` frontmatter and trigger wording, and this "How it works" /
  "Steps" content:

```markdown
---
name: context-override
description: Silence the context-window-warning Stop hook's 100k/150k warnings entirely for the current session. Use when the user runs /context-override, or says "stop the context warnings", "silence the compact nag", "I know about the context, stop warning me". By default the warnings are visible but non-blocking; this makes them silent. Run with "off" to re-enable warnings, "status" to check.
---

# context-override

Toggles a per-session flag that the `context-window-warning` Stop hook checks.
When ON, the hook still surfaces a gentle "⚠ Context ~Xk" reminder each turn but
never pauses work, so file reads and other tool calls proceed normally past the
100k/150k thresholds.

## When to use

- The user types `/context-override` -> default to turning it **ON**.
- The user says "stop the context warnings", "silence the compact nag", or otherwise
  wants the per-turn warnings to stop appearing.
- Note: warnings are non-blocking by default. Override silences them entirely - useful
  when the user is aware of the situation and doesn't want per-turn noise.
- `/context-override off` (or "re-enable context warnings") -> turn it **OFF**.
- `/context-override status` -> report the current state.

## How it works

The flag lives in a `context_overrides` row in CCST's shared `sessions.db`, keyed by
`$CLAUDE_CODE_SESSION_ID`. The Stop hook (`ccst hooks run context-window-warning`) reads
that state and, when `on`, exits silently - no warning is injected and the session stops
normally. Warnings are visible but non-blocking by default; override makes them completely
silent. The override does not persist across sessions - a new session starts with
warnings active again. Running `/compact` does not clear it; use
`/context-override off` to re-enable warnings mid-session.

## Steps

1. Pick the action from the user's words: `on` (default), `off`, or `status`.
2. Run:

   ```bash
   ccst context-override <action>
   ```

3. Relay the command's output to the user as a single confirmation line.
```

- [ ] **Step 2:** Verify `ccst skills install --apply --target <tmp dir>` symlinks it correctly (do
  not run against the real `~/.claude/skills` yet — that's Task 11):
  ```bash
  mkdir -p /tmp/ccst-skills-smoke && uv run python -m cc_session_tools.cli.ccst skills install --apply --target /tmp/ccst-skills-smoke && ls -la /tmp/ccst-skills-smoke/context-override
  ```
  Expected: a symlink into the worktree's installed package tree, resolving to the new `SKILL.md`.

- [ ] **Step 3: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add src/cc_session_tools/skills/context-override/SKILL.md && git commit -m "feat(skills): port context-override skill into CCST

No scripts/ directory - Task 7's 'ccst context-override' subcommand IS
the mechanism now, so the skill's only job is to tell Claude to invoke
it. Same triggers and description as the CCCS original; 'How it works'
updated to describe the sessions.db-backed store instead of the flag
file."
```

---

### Task 10: Docs — README, CHANGELOG, module docstrings

§"Documents to update".

**Files:** `README.md`, `CHANGELOG.md`, `src/cc_session_tools/cli/ccst.py` (docstring only, if not
already done inline in Task 7)

- [ ] **Step 1:** `README.md` `## Bundled skills` (~L425) — add a `### context-override` entry
  matching the format of its neighbours (one paragraph: what it wraps, its triggers).

- [ ] **Step 2:** `README.md` `## Hook library (cccs_hooks)` (~L578) — add rows to both tables:
  - Module table: `cccs_hooks.context_window_warning` / `Stop` / one-line description; and
    `cccs_hooks.model_info` / `—` / "model id → context window / price / display name lookup".
  - `ccst hooks run <name>` verb table: `context-window-warning` / `cccs_hooks.context_window_warning`.

- [ ] **Step 3:** `README.md` `## Hook management CLI (ccst)` (~L721) — document
  `ccst context-override [on|off|status]` alongside the other subcommands there.

- [ ] **Step 4:** `CHANGELOG.md` — new entry under `## [Unreleased]` (the version header gets filled
  in at Task 12 once the bump number is confirmed):
  ```markdown
  ### Added

  - **`ccst hooks run context-window-warning`** and **`ccst context-override [on|off|status]`** —
    the context-window Stop-hook nudge and its override skill, previously owned by
    `claude-code-config-sync`, now live natively in CCST with identical behaviour (same 150k/200k
    thresholds, same message wording). The override flag moved from a
    `~/.claude/context-overrides/<session_id>` file to a `context_overrides` row in the shared
    `sessions.db`.
  ```

- [ ] **Step 5:** `uv run pytest -q` (docs don't break tests, but confirms nothing else regressed
  before the doc-only commit).

- [ ] **Step 6: Commit**
```bash
cd ~/repos/claude-code-session-tools/.worktrees/context-window-warning-to-ccst && git add README.md CHANGELOG.md src/cc_session_tools/cli/ccst.py && git commit -m "docs: document the ported context-window-warning hook and context-override skill/CLI"
```

---

### Task 11: Ship — clear the stale real directory, install, verify

§7. **Do not run this task until you have explicit go-ahead to ship** (this plan's review gate).

**Files:** none (environment) — run from the merged worktree / `main`, not mid-review.

- [ ] **Step 1:** Confirm the target is still a real directory (guards against re-running this after
  it's already been cleared, or against a surprise symlink someone else created):
  ```bash
  ls -la ~/.claude/skills/context-override
  ```
  Expected: `drwx------` (a real directory). If it is already a symlink, **stop** — something else
  already migrated it; investigate before proceeding.

- [ ] **Step 2:** Remove it (safe — its replacement is a symlink to the newly-shipped skill):
  ```bash
  rm -rf ~/.claude/skills/context-override
  ```

- [ ] **Step 3:** `uv tool install --reinstall ~/repos/claude-code-session-tools` (per this repo's
  own `.claude/CLAUDE.md` — `--reinstall` is required, a same-version rebuild is otherwise skipped).

- [ ] **Step 4:** `ccst install-everything --apply` and confirm the five-step output shows the new
  hook entry added and the skill symlinked (not "already up to date" — if it says that, something
  is wrong, stop and investigate before continuing).

- [ ] **Step 5:** Verify `~/.claude/settings.json`'s `Stop` array now carries **both** the old CCCS
  entry and the new `ccst hooks run context-window-warning` entry (§8's expected transitional
  overlap — not a bug):
  ```bash
  python3 -c "import json; d=json.load(open('$HOME/.claude/settings.json')); print(json.dumps(d['hooks']['Stop'], indent=2))"
  ```

- [ ] **Step 6:** `ccst doctor` — confirm no new FAIL/WARN introduced by this change.

- [ ] **Step 7:** Smoke-test `/context-override` and the hook end-to-end in a real session (run
  `ccst context-override status`, then a turn that pushes context past 150k, confirm the 🟠 message
  appears with the new implementation's wording matching the old one; `ccst context-override on`,
  confirm it goes silent).

---

### Task 12: Version bump (number pending your review — see spec §9)

**Do not run until the spec §9 question is answered.**

- [ ] **Step 1:** `pyproject.toml` — bump `version` to whichever number you confirmed (2.5.1 or
  2.6.0).
- [ ] **Step 2:** `CHANGELOG.md` — rename `## [Unreleased]` to `## [<version>] - <date>` for this
  entry (matching the existing convention — see the `[2.5.0]` entry for exact format).
- [ ] **Step 3:** Commit, following the rest of this repo's release flow (`.claude/CLAUDE.md`'s
  "Releasing" section) — tag, push, GitHub Release triggers the PyPI publish.

---

## Notes for whoever executes this plan

- Tasks 0-10 build and land entirely inside the worktree with normal TDD checkpoints; nothing
  touches the live `~/.claude` install until Task 11.
- Task 11 is the one irreversible-ish step (real directory deletion) — confirm with the user before
  running it, even though it is scoped to a directory this change is deliberately replacing.
- The cross-session message to CCCS (id `20260817T212309Z-5428`) asked it to hold removal until this
  ships. After Task 11 completes, a short follow-up message to CCCS confirming "2.5.1/2.6.0 shipped,
  go ahead" is warranted — not automated by this plan, since it depends on Task 11 actually
  succeeding first.
