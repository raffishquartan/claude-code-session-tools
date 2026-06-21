# Scheduled-tasks Catch-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make periodic local jobs (Tesco checks, calendar syncs, and any future argv) run on a declared cadence on a laptop that is frequently off, by turning the scheduler into a *reconciler* triggered on Claude Code `SessionStart`. On each session start the sweep computes which scheduled runs were missed while the machine was off and back-fills them — running once-late or coalescing N misses into one, per each job's declared semantics — without blocking or noticeably slowing session start, and surfaces what ran / was missed / failed as an injected digest. Ships inside CCST with the same packaging/installer/upgrade story as existing tooling.

**Architecture:** A hand-curatable TOML registry (`~/.claude/cc-scheduler/jobs.toml`) of `[[job]]` records and a per-job `state.json` (registered_at / last_success / last_attempt / consecutive_failures). A new `src/cc_session_tools/lib/scheduler/` package holds all logic: a cadence-grammar parser, a duration parser, **pure** due-computation (`owed(...)` with an injected `now`), coalescing semantics, atomic `state.json` I/O, an `O_EXCL` sweep lock with stale-lock reclamation, a per-job subprocess runner, and digest formatting. A new `ccsched` CLI (thin argparse layer matching `ccd.py`/`ccst.py` conventions) is the management surface. The reconcile sweep is a hook module `src/cccs_hooks/catchup.py` run via `ccst hooks run catchup`, registered on `SessionStart` in `config/hooks-bundle.json` and merged via `merge_hook_settings`. Every sweep action is one JSONL line appended to the **existing** `~/.claude/hooks/fires.jsonl` telemetry ledger (reused, not reinvented). A single skill `manage-recurring-cc-jobs-using-ccsched` translates natural-language requests into validated `ccsched add` calls and disambiguates the three schedulers (`ccsched` vs `/schedule` vs `/loop`).

**Tech Stack:** Python 3.11+, stdlib `tomllib` for reading TOML + a hand-rolled minimal TOML *writer* (registry is small, structured, and we control the schema — no third-party TOML-writer dependency), argparse (no click/typer), `dataclass(frozen=True, slots=True)` value objects, `enum.Enum` for closed sets (coalesce kind, ledger event kind, cadence kind), stdlib `os.open(O_CREAT|O_EXCL)` for the sweep lock, atomic `.tmp`-swap writes, `subprocess.run` with `timeout` for the runner, `pytest` + `tmp_path` + `monkeypatch` + subprocess, `mypy --strict`. Reuses `cccs_hooks/telemetry.py` (`TelemetryEntry` / `log_event`) for the ledger, and the `hooks_install.merge_hook_settings` / `write_json_atomic` helpers.

---

## Conventions every task must follow

- **Type hints** on every function signature and module-level constant. `from __future__ import annotations` at the top of every module. Run `mypy --strict` clean.
- **No personal paths** in committed code or tests. Use `Path.home()` at runtime; fictional placeholders (`/home/alice`, `/example/repos/project`) in tests. Never `/home/chris`.
- **en-GB spelling** in prose/comments (organise, behaviour, colour) to match the repo.
- **Validation at the CLI/schema boundary only** (argparse + a single validator producing a typed `JobSpec`); internals trust validated input. No re-validation inside lib functions.
- **Pure logic separate from I/O.** Cadence math (`owed`, duration/cadence parsing) is pure and unit-tested with an injected `now`. File I/O (registry/state read-write, the runner subprocess, the lock) lives in separate modules. `logging` (named logger), never `print`, in library code; the CLI and hook are the only modules that write to stdout/stderr.
- **No defensive code for impossible states.** Raise specific exceptions (`CadenceError`, `DurationError`, `JobValidationError`, `RegistryError`, `SweepLockHeld`) with clear messages. Never `except Exception: pass`. The **catchup hook is the only place** that converts errors to an empty `additionalContext` — and even there it logs to telemetry, never silently swallows.
- **Closed sets are enums:** `CoalesceKind {ONE, EACH}`, `LedgerEvent {RUN, BACKFILL, SKIP_EXPIRED, DEFER, FAIL}`, `CadenceKind {EVERY, DAILY, WEEKLY, MONTHLY}`.
- **Atomic state writes** via a `.tmp`-swap helper (mirrors `hooks_install.write_json_atomic`). State transitions are never naive rewrites.
- **Every `try/except` in a handler gets a failure-path test.** Every validation branch gets a test.
- **Commit after every green test.** Each task's commit command is written out; end every commit message body with a line containing only `[Cld]`.

> **Note for the implementer:** every `git commit` command below is for *you* to run while executing the plan. The plan author did not run them. Work on a feature branch created via @superpowers:using-git-worktrees before you start Task 0. Use `uv run pytest -q` and `uv run` for everything — never `uv tool install` from a worktree (it overwrites the global install's source pointer; see the project CLAUDE.md).

---

## File Structure

**New library package — `src/cc_session_tools/lib/scheduler/`** (one responsibility per module):

- `src/cc_session_tools/lib/scheduler/__init__.py` — package marker; re-exports nothing (avoid barrel pull-in of I/O modules).
- `src/cc_session_tools/lib/scheduler/duration.py` — `parse_duration(text) -> timedelta` for the `<int><s|m|h|d>` grammar; `DurationError`. Pure.
- `src/cc_session_tools/lib/scheduler/cadence.py` — `Cadence` value object + `CadenceKind` enum + `parse_cadence(text) -> Cadence` for `every:/daily@/weekly:/monthly:` (§7); `CadenceError`. Pure.
- `src/cc_session_tools/lib/scheduler/due.py` — pure due-computation: `owed(cadence, baseline, now, catchup_window) -> OwedResult` returning the in-window scheduled instants in `(baseline, now]` plus the count dropped as expired. No I/O, `now` injected. Coalescing is applied by the caller using `CoalesceKind`.
- `src/cc_session_tools/lib/scheduler/jobspec.py` — `JobSpec` (`frozen=True, slots=True`) value object (id, cadence, coalesce, command, surface, enabled, catchup_window, timeout) + `CoalesceKind` enum + `validate_job_fields(...) -> JobSpec` (the single boundary validator). `JobValidationError`.
- `src/cc_session_tools/lib/scheduler/registry.py` — `jobs.toml` read (`tomllib`) and write (minimal hand-rolled serialiser) with atomic `.tmp`-swap; `load_registry() -> list[JobSpec]`, `add_job`, `replace_job`, `remove_job`, `set_enabled`; `RegistryError` for unparseable TOML.
- `src/cc_session_tools/lib/scheduler/state.py` — `JobState` value object + per-job `state.json` I/O (registered_at / last_success / last_attempt / consecutive_failures) via atomic `.tmp`-swap; `load_state`, `save_state`, helpers to stamp `registered_at` lazily.
- `src/cc_session_tools/lib/scheduler/lock.py` — `O_EXCL` sweep lock at `~/.claude/cc-scheduler/.sweep.lock` as a context manager; stores holder pid + start time; stale-lock reclamation (dead pid → reclaim); `SweepLockHeld`.
- `src/cc_session_tools/lib/scheduler/runner.py` — `run_command(argv, timeout) -> RunOutcome` (subprocess argv + timeout kill); pure-ish wrapper around `subprocess.run`, returns exit code / stdout / duration / timed-out flag. No state mutation.
- `src/cc_session_tools/lib/scheduler/ledger.py` — thin adapter over `cccs_hooks.telemetry` mapping a scheduler event to a `TelemetryEntry` line (`hook="catchup"`, fields `job_id`, `event`, `owed`, `ran`, `exit_code`, `duration_ms`, `error` packed into `verdict`); plus `read_recent(job_id=?)` for `ccsched status`.
- `src/cc_session_tools/lib/scheduler/digest.py` — pure digest formatting (§11): per-job lines (`✓ ran ...`, `✗ ... failed`, `⏳ ... deferred`, `skip_expired`, the unparseable-registry warning). Takes structured sweep results, returns a string. No I/O.
- `src/cc_session_tools/lib/scheduler/sweep.py` — the reconcile orchestration (§9): per enabled job in registry order, compute owed (stamping `registered_at` lazily), apply coalescing, run within the time budget + per-sweep cap, update state, emit ledger events, collect digest results. Takes injected `now` and a clock-deadline so it is testable without real wall-clock sleeps. Holds the lock for the whole sweep. This is where lib I/O is composed; the hook is a thin wrapper.

**New CLI:**

- `src/cc_session_tools/cli/ccsched.py` — thin argparse layer (`_build_parser()`, `main(argv=None) -> int`, `--version`) dispatching to the lib. Subcommands: `add`, `list`, `edit`, `enable`, `disable`, `remove`, `run`, `status`, `sweep`. Validation lives here (delegating to `jobspec.validate_job_fields`).

**New hook:**

- `src/cccs_hooks/catchup.py` — reads stdin JSON, runs `sweep.run_sweep(...)`, emits `additionalContext` digest. Never raises; on any failure emits empty context + logs to telemetry.

**Modified files:**

- `src/cc_session_tools/cli/ccst.py` — add `catchup` to `HOOK_VERBS` + `HOOK_DESCRIPTIONS`.
- `config/hooks-bundle.json` — add the `catchup` entry to `SessionStart`.
- `pyproject.toml` — add `ccsched` script; add scheduler test path to `[tool.pytest.ini_options].testpaths`; bump version 0.13.0 → 0.14.0.
- `install-everything.sh` — no new top-level step (registry is lazy-created); steps 1–3 already pick up the new CLI/skill/hook idempotently. Verify only.
- `README.md` — new `## Scheduled-task catch-up` section + skill/hook/CLI table entries.
- `CHANGELOG.md` — `### Added` entries under a new `## [0.14.0]` block.

**New skill (doc-only, no tests):**

- `skills/manage-recurring-cc-jobs-using-ccsched/SKILL.md`.

**New tests** (all under existing `tests/` tree; a new `tests/scheduler/` package added to `testpaths`):

- `tests/scheduler/__init__.py`, `tests/scheduler/test_duration.py`, `test_cadence.py`, `test_due.py`, `test_jobspec.py`, `test_registry.py`, `test_state.py`, `test_lock.py`, `test_runner.py`, `test_ledger.py`, `test_digest.py`, `test_sweep.py`, `test_ccsched_cli.py`, `test_catchup_hook.py`.

---

# Phase A — pure logic (no I/O)

All Phase A modules are pure and unit-testable with no filesystem access. Create the package marker first.

### Task 0 (setup): scaffold the scheduler package and test dir

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/__init__.py`
- Create: `tests/scheduler/__init__.py` *(empty; lets pytest import the test package cleanly)*
- Modify: `pyproject.toml` (`testpaths` — add `tests/scheduler` is not needed since `tests` is already a testpath root; confirm `tests/scheduler` is collected)

> The repo's `testpaths` already includes `"tests"`, so `tests/scheduler/` is collected automatically. No `pyproject.toml` change is needed in this task — the `ccsched` script + version bump land in Task 12. This task is pure scaffolding.

- [ ] **Step 1: Create the package files**

```python
# src/cc_session_tools/lib/scheduler/__init__.py
"""Scheduled-tasks catch-up library: cadence/duration parsing, pure
due-computation, coalescing, registry + state I/O, sweep lock, the per-job
runner, the telemetry-ledger adapter, digest formatting, and the reconcile
sweep orchestration."""
```

```python
# tests/scheduler/__init__.py  (empty file)
```

- [ ] **Step 2: Confirm collection**

Run: `uv run pytest tests/scheduler -q`
Expected: `no tests ran` (collection succeeds, zero tests) — confirms the package imports cleanly.

- [ ] **Step 3: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/__init__.py tests/scheduler/__init__.py
git commit -m "chore: scaffold scheduler package and test dir

[Cld]"
```

---

### Task 1: `duration.py` — `parse_duration`

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/duration.py`
- Test: `tests/scheduler/test_duration.py`

**Responsibilities:** parse `<int><unit>` where unit ∈ `s|m|h|d` into a `timedelta`; reject empty, zero, negative, missing-unit, bad-unit, and non-integer forms with `DurationError`. Pure, no I/O.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_duration.py
from __future__ import annotations

from datetime import timedelta

import pytest

from cc_session_tools.lib.scheduler.duration import DurationError, parse_duration


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("6h", timedelta(hours=6)),
        ("7d", timedelta(days=7)),
        ("1d", timedelta(days=1)),
    ],
)
def test_parse_valid(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "0s", "-5m", "5", "h", "5x", "5.5h", "5 h", "abc"])
def test_parse_invalid_raises(text: str) -> None:
    with pytest.raises(DurationError):
        parse_duration(text)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_duration.py -q`
Expected: FAIL with `ModuleNotFoundError: cc_session_tools.lib.scheduler.duration`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/duration.py
"""Parse the ``<int><unit>`` duration grammar (units s/m/h/d) into a
``timedelta``. Pure; raises ``DurationError`` on any malformed input."""
from __future__ import annotations

import re
from datetime import timedelta

_DURATION_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[smhd])$")
_UNIT_KW = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


class DurationError(ValueError):
    """Raised when a duration string does not match ``<positive-int><s|m|h|d>``."""


def parse_duration(text: str) -> timedelta:
    match = _DURATION_RE.match(text)
    if match is None:
        raise DurationError(
            f"invalid duration {text!r}: expected <positive-integer><s|m|h|d>, e.g. '6h'"
        )
    value = int(match.group("value"))
    if value <= 0:
        raise DurationError(f"invalid duration {text!r}: value must be positive")
    return timedelta(**{_UNIT_KW[match.group("unit")]: value})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_duration.py -q`
Expected: PASS (10 cases).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/duration.py tests/scheduler/test_duration.py
git commit -m "feat(scheduler): duration parser (<int><s|m|h|d>)

[Cld]"
```

---

### Task 2: `cadence.py` — `Cadence` + `parse_cadence`

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/cadence.py`
- Test: `tests/scheduler/test_cadence.py`

**Responsibilities:** a `Cadence` value object (`frozen=True, slots=True`) carrying a `CadenceKind` plus the parsed fields (interval `timedelta` for `every`; `hour`/`minute` for the wall-clock forms; `dow` 0–6 for weekly; `dom` 1–31 for monthly). `parse_cadence(text)` parses the four §7 forms and raises `CadenceError` for anything else (including a `cron:` escape hatch, which is deferred per §7/§18). Pure; reuses `parse_duration` for `every:`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_cadence.py
from __future__ import annotations

from datetime import timedelta

import pytest

from cc_session_tools.lib.scheduler.cadence import (
    Cadence,
    CadenceError,
    CadenceKind,
    parse_cadence,
)


def test_every_parses_interval() -> None:
    c = parse_cadence("every:6h")
    assert c.kind is CadenceKind.EVERY
    assert c.interval == timedelta(hours=6)


def test_daily_parses_wall_clock() -> None:
    c = parse_cadence("daily@09:00")
    assert c.kind is CadenceKind.DAILY
    assert (c.hour, c.minute) == (9, 0)


def test_weekly_parses_dow_and_time() -> None:
    c = parse_cadence("weekly:mon@07:30")
    assert c.kind is CadenceKind.WEEKLY
    assert c.dow == 0  # Monday
    assert (c.hour, c.minute) == (7, 30)


def test_weekly_accepts_sunday() -> None:
    assert parse_cadence("weekly:sun@23:59").dow == 6


def test_monthly_parses_dom_and_time() -> None:
    c = parse_cadence("monthly:1@00:00")
    assert c.kind is CadenceKind.MONTHLY
    assert c.dom == 1
    assert (c.hour, c.minute) == (0, 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "every:",
        "every:0h",
        "daily@9",            # missing minutes
        "daily@24:00",        # hour out of range
        "daily@09:60",        # minute out of range
        "weekly:funday@09:00",
        "weekly:mon",         # missing time
        "monthly:0@09:00",    # dom < 1
        "monthly:32@09:00",   # dom > 31
        'cron:"0 9 * * *"',   # deferred escape hatch
        "hourly@09:00",
    ],
)
def test_invalid_raises(text: str) -> None:
    with pytest.raises(CadenceError):
        parse_cadence(text)


def test_cadence_is_frozen() -> None:
    c = parse_cadence("daily@09:00")
    with pytest.raises(AttributeError):
        c.hour = 10  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_cadence.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/cadence.py
"""Cadence grammar (§7): every:/daily@/weekly:/monthly:.

Wall-clock forms (@HH:MM) are interpreted in local time by the due-computation;
this module only parses them into a typed ``Cadence`` value object. Pure;
raises ``CadenceError`` on any malformed input. A ``cron:`` escape hatch is
deliberately not supported (deferred — see §18)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from cc_session_tools.lib.scheduler.duration import DurationError, parse_duration


class CadenceError(ValueError):
    """Raised when a cadence string does not match a supported form."""


class CadenceKind(str, Enum):
    EVERY = "every"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_DAILY_RE = re.compile(r"^daily@(?P<h>\d{2}):(?P<m>\d{2})$")
_WEEKLY_RE = re.compile(r"^weekly:(?P<dow>[a-z]{3})@(?P<h>\d{2}):(?P<m>\d{2})$")
_MONTHLY_RE = re.compile(r"^monthly:(?P<dom>\d{1,2})@(?P<h>\d{2}):(?P<m>\d{2})$")


@dataclass(frozen=True, slots=True)
class Cadence:
    kind: CadenceKind
    interval: timedelta | None = None  # EVERY
    hour: int | None = None            # wall-clock forms
    minute: int | None = None
    dow: int | None = None             # WEEKLY (0=Mon..6=Sun)
    dom: int | None = None             # MONTHLY (1..31)


def _hm(h: str, m: str, text: str) -> tuple[int, int]:
    hour, minute = int(h), int(m)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CadenceError(f"invalid time in cadence {text!r}: HH:MM out of range")
    return hour, minute


def parse_cadence(text: str) -> Cadence:
    if text.startswith("every:"):
        try:
            interval = parse_duration(text[len("every:"):])
        except DurationError as exc:
            raise CadenceError(f"invalid every: cadence {text!r}: {exc}") from exc
        return Cadence(kind=CadenceKind.EVERY, interval=interval)

    daily = _DAILY_RE.match(text)
    if daily:
        hour, minute = _hm(daily.group("h"), daily.group("m"), text)
        return Cadence(kind=CadenceKind.DAILY, hour=hour, minute=minute)

    weekly = _WEEKLY_RE.match(text)
    if weekly:
        dow_name = weekly.group("dow")
        if dow_name not in _DOW:
            raise CadenceError(f"invalid day-of-week in {text!r}: use mon..sun")
        hour, minute = _hm(weekly.group("h"), weekly.group("m"), text)
        return Cadence(kind=CadenceKind.WEEKLY, dow=_DOW[dow_name], hour=hour, minute=minute)

    monthly = _MONTHLY_RE.match(text)
    if monthly:
        dom = int(monthly.group("dom"))
        if not (1 <= dom <= 31):
            raise CadenceError(f"invalid day-of-month in {text!r}: use 1..31")
        hour, minute = _hm(monthly.group("h"), monthly.group("m"), text)
        return Cadence(kind=CadenceKind.MONTHLY, dom=dom, hour=hour, minute=minute)

    raise CadenceError(
        f"unrecognised cadence {text!r}: expected every:<dur> / daily@HH:MM / "
        "weekly:<dow>@HH:MM / monthly:<dom>@HH:MM"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_cadence.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/cadence.py tests/scheduler/test_cadence.py
git commit -m "feat(scheduler): cadence grammar parser (every/daily/weekly/monthly)

[Cld]"
```

---

### Task 3: `due.py` — pure due-computation `owed(...)`

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/due.py`
- Test: `tests/scheduler/test_due.py`

**Responsibilities:** the pure heart of the reconciler. `owed(cadence, baseline, now, catchup_window) -> OwedResult` enumerates the scheduled instants in `(baseline, now]` per the cadence (all timestamps `datetime`; wall-clock forms computed in **local** time but compared in UTC-aware datetimes), then partitions them into `instants` (within `now - catchup_window`) and `expired_count` (older than the window, to be logged `skip_expired`). `now` is injected — no `datetime.now()` call. Also `next_due(cadence, baseline, now)` for `ccsched list`. DST policy (§17.3): wall-clock instants are computed naively on the local calendar (one instant per calendar day/week/month occurrence); an instant that does not exist on a DST-skip day still produces exactly one owed instant at the nominal local time mapped to UTC, so a job fires once per period and never twice.

> **Implementer note on local time:** use `datetime.now().astimezone()` semantics only via the injected `now` (which carries tzinfo). Build candidate instants on the local calendar using `now.astimezone()`'s local date, attach the same tzinfo, then compare. Keep the arithmetic explicit and small; do not pull in `dateutil`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_due.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.due import OwedResult, next_due, owed

UTC = timezone.utc


def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_every_no_misses_when_within_interval() -> None:
    c = parse_cadence("every:6h")
    baseline = _dt(2026, 6, 20, 9, 0)
    now = _dt(2026, 6, 20, 12, 0)  # only 3h later
    result = owed(c, baseline, now, catchup_window=timedelta(days=7))
    assert result.instants == []
    assert result.expired_count == 0


def test_every_counts_each_elapsed_interval() -> None:
    c = parse_cadence("every:6h")
    baseline = _dt(2026, 6, 20, 0, 0)
    now = _dt(2026, 6, 20, 19, 0)  # 6h,12h,18h are owed → 3
    result = owed(c, baseline, now, catchup_window=timedelta(days=7))
    assert len(result.instants) == 3


def test_daily_misses_across_three_days() -> None:
    c = parse_cadence("daily@09:00")
    baseline = _dt(2026, 6, 17, 9, 0)         # last success on the 17th at 09:00
    now = _dt(2026, 6, 20, 10, 0)             # 18th, 19th, 20th 09:00 owed → 3
    result = owed(c, baseline, now, catchup_window=timedelta(days=30))
    assert len(result.instants) == 3


def test_catchup_window_drops_expired_instants() -> None:
    c = parse_cadence("daily@09:00")
    baseline = _dt(2026, 6, 1, 9, 0)
    now = _dt(2026, 6, 20, 10, 0)
    # 19 daily instants owed, but only the last 7 days are in-window.
    result = owed(c, baseline, now, catchup_window=timedelta(days=7))
    assert len(result.instants) == 7
    assert result.expired_count == 12


def test_weekly_one_per_week() -> None:
    c = parse_cadence("weekly:mon@09:00")
    baseline = _dt(2026, 6, 1, 9, 0)          # Mon 2026-06-01
    now = _dt(2026, 6, 22, 10, 0)             # Mons 08,15,22 owed → 3
    result = owed(c, baseline, now, catchup_window=timedelta(days=60))
    assert len(result.instants) == 3


def test_monthly_one_per_month() -> None:
    c = parse_cadence("monthly:1@09:00")
    baseline = _dt(2026, 4, 1, 9, 0)
    now = _dt(2026, 6, 2, 10, 0)              # May 1 + Jun 1 owed → 2
    result = owed(c, baseline, now, catchup_window=timedelta(days=120))
    assert len(result.instants) == 2


def test_next_due_after_baseline() -> None:
    c = parse_cadence("daily@09:00")
    baseline = _dt(2026, 6, 20, 9, 0)
    now = _dt(2026, 6, 20, 12, 0)
    assert next_due(c, baseline, now) == _dt(2026, 6, 21, 9, 0)
```

> The fixtures use UTC as the local zone so the wall-clock arithmetic is deterministic in CI regardless of the runner's `TZ`. Add one explicit DST-region test only if the implementer's local arithmetic uses `zoneinfo`; if the implementation keeps everything in the injected tz, document that real-DST behaviour is covered by the naive-calendar contract above.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_due.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/due.py
"""Pure due-computation. Given a cadence, a baseline (last success or
registered_at), and an injected ``now``, enumerate the scheduled instants in
``(baseline, now]`` and split them into in-window instants and an expired count
(older than ``now - catchup_window``). No I/O, no ``datetime.now()`` call.

Wall-clock cadences are computed on the local calendar of the injected ``now``
(one instant per calendar occurrence); see §17.3 for the DST contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from cc_session_tools.lib.scheduler.cadence import Cadence, CadenceKind


@dataclass(frozen=True, slots=True)
class OwedResult:
    instants: list[datetime]
    expired_count: int


def _every_instants(interval: timedelta, baseline: datetime, now: datetime) -> list[datetime]:
    out: list[datetime] = []
    nxt = baseline + interval
    while nxt <= now:
        out.append(nxt)
        nxt = nxt + interval
    return out


def _at_local(day: datetime, hour: int, minute: int) -> datetime:
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _daily_instants(c: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    assert c.hour is not None and c.minute is not None
    out: list[datetime] = []
    cur = _at_local(baseline, c.hour, c.minute)
    if cur <= baseline:
        cur = cur + timedelta(days=1)
    while cur <= now:
        out.append(cur)
        cur = cur + timedelta(days=1)
    return out


def _weekly_instants(c: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    assert c.hour is not None and c.minute is not None and c.dow is not None
    out: list[datetime] = []
    cur = _at_local(baseline, c.hour, c.minute)
    # Advance to the first matching weekday strictly after baseline.
    while cur.weekday() != c.dow or cur <= baseline:
        cur = cur + timedelta(days=1)
        cur = _at_local(cur, c.hour, c.minute)
    while cur <= now:
        out.append(cur)
        cur = cur + timedelta(days=7)
    return out


def _add_month(dt: datetime) -> datetime:
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return dt.replace(year=year, month=month)


def _monthly_instants(c: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    assert c.hour is not None and c.minute is not None and c.dom is not None
    out: list[datetime] = []
    cur = _at_local(baseline.replace(day=1), c.hour, c.minute)
    while cur <= now:
        try:
            candidate = cur.replace(day=c.dom)
        except ValueError:
            candidate = None  # dom does not exist this month (e.g. Feb 30) → skip
        if candidate is not None and baseline < candidate <= now:
            out.append(candidate)
        cur = _add_month(cur)
    return out


def _all_instants(cadence: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    if cadence.kind is CadenceKind.EVERY:
        assert cadence.interval is not None
        return _every_instants(cadence.interval, baseline, now)
    if cadence.kind is CadenceKind.DAILY:
        return _daily_instants(cadence, baseline, now)
    if cadence.kind is CadenceKind.WEEKLY:
        return _weekly_instants(cadence, baseline, now)
    return _monthly_instants(cadence, baseline, now)


def owed(
    cadence: Cadence,
    baseline: datetime,
    now: datetime,
    *,
    catchup_window: timedelta,
) -> OwedResult:
    cutoff = now - catchup_window
    instants = _all_instants(cadence, baseline, now)
    in_window = [i for i in instants if i >= cutoff]
    return OwedResult(instants=in_window, expired_count=len(instants) - len(in_window))


def next_due(cadence: Cadence, baseline: datetime, now: datetime) -> datetime:
    """The first scheduled instant strictly after ``max(baseline, now)``."""
    anchor = max(baseline, now)
    # Reuse the enumerators by projecting one period past the anchor.
    if cadence.kind is CadenceKind.EVERY:
        assert cadence.interval is not None
        nxt = baseline
        while nxt <= anchor:
            nxt = nxt + cadence.interval
        return nxt
    far = anchor + timedelta(days=400)
    future = [i for i in _all_instants(cadence, anchor, far)]
    return future[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_due.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/due.py tests/scheduler/test_due.py
git commit -m "feat(scheduler): pure due-computation owed() + next_due()

[Cld]"
```

---

### Task 4: `jobspec.py` — `JobSpec`, `CoalesceKind`, boundary validator

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/jobspec.py`
- Test: `tests/scheduler/test_jobspec.py`

**Responsibilities:** the typed job record + the single boundary validator. `validate_job_fields(id, cadence, coalesce, command, surface, enabled, catchup_window, timeout) -> JobSpec` enforces: unique-kebab `id` shape (lower, `[a-z0-9-]`, no leading/trailing dash), parseable cadence (delegates to `parse_cadence`), non-empty `command` (argv list ≥ 1, all non-empty strings), `coalesce ∈ {one, each}`, positive `catchup_window`/`timeout` durations (delegates to `parse_duration`). Raises `JobValidationError` with a clear message per failing field. Uniqueness against the existing registry is checked by the registry layer (Task 5), not here — this validates one record's shape.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_jobspec.py
from __future__ import annotations

import pytest

from cc_session_tools.lib.scheduler.jobspec import (
    CoalesceKind,
    JobSpec,
    JobValidationError,
    validate_job_fields,
)


def _valid() -> JobSpec:
    return validate_job_fields(
        job_id="tesco-shop-check",
        cadence="daily@09:00",
        coalesce="one",
        command=["ccst", "hooks", "run", "check-tesco-due"],
        surface=True,
        enabled=True,
        catchup_window="7d",
        timeout="60s",
    )


def test_valid_record_builds() -> None:
    spec = _valid()
    assert spec.job_id == "tesco-shop-check"
    assert spec.coalesce is CoalesceKind.ONE
    assert spec.command == ("ccst", "hooks", "run", "check-tesco-due")


def test_jobspec_is_frozen() -> None:
    spec = _valid()
    with pytest.raises(AttributeError):
        spec.job_id = "x"  # type: ignore[misc]


@pytest.mark.parametrize("bad_id", ["", "Tesco", "-lead", "trail-", "has space", "под"])
def test_bad_id_rejected(bad_id: str) -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id=bad_id, cadence="daily@09:00", coalesce="one",
            command=["x"], surface=True, enabled=True,
            catchup_window="7d", timeout="60s",
        )


def test_bad_cadence_rejected() -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id="j", cadence="hourly", coalesce="one", command=["x"],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )


def test_empty_command_rejected() -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id="j", cadence="daily@09:00", coalesce="one", command=[],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )


def test_command_with_empty_arg_rejected() -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id="j", cadence="daily@09:00", coalesce="one", command=["ok", ""],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )


def test_bad_coalesce_rejected() -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id="j", cadence="daily@09:00", coalesce="sometimes", command=["x"],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )


@pytest.mark.parametrize("field,bad", [("catchup_window", "0d"), ("timeout", "-5s")])
def test_bad_durations_rejected(field: str, bad: str) -> None:
    kwargs = dict(
        job_id="j", cadence="daily@09:00", coalesce="one", command=["x"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
    )
    kwargs[field] = bad
    with pytest.raises(JobValidationError):
        validate_job_fields(**kwargs)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_jobspec.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/jobspec.py
"""The typed job record and the single boundary validator. Once a JobSpec is
built, internals trust it; no re-validation downstream."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from cc_session_tools.lib.scheduler.cadence import CadenceError, parse_cadence
from cc_session_tools.lib.scheduler.duration import DurationError, parse_duration

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class JobValidationError(ValueError):
    """Raised when a job record's fields fail boundary validation."""


class CoalesceKind(str, Enum):
    ONE = "one"
    EACH = "each"


@dataclass(frozen=True, slots=True)
class JobSpec:
    job_id: str
    cadence: str
    coalesce: CoalesceKind
    command: tuple[str, ...]
    surface: bool
    enabled: bool
    catchup_window: str
    timeout: str


def _check_id(job_id: str) -> None:
    if not _KEBAB_RE.match(job_id):
        raise JobValidationError(
            f"invalid job id {job_id!r}: must be lowercase kebab-case [a-z0-9-], "
            "no leading/trailing dash"
        )


def _check_command(command: list[str]) -> tuple[str, ...]:
    if len(command) < 1:
        raise JobValidationError("command must have at least one argv element")
    if any(not part for part in command):
        raise JobValidationError("command argv elements must all be non-empty")
    return tuple(command)


def _check_coalesce(coalesce: str) -> CoalesceKind:
    try:
        return CoalesceKind(coalesce)
    except ValueError as exc:
        raise JobValidationError(
            f"invalid coalesce {coalesce!r}: must be 'one' or 'each'"
        ) from exc


def _check_positive_duration(name: str, value: str) -> None:
    try:
        parse_duration(value)
    except DurationError as exc:
        raise JobValidationError(f"invalid {name} {value!r}: {exc}") from exc


def validate_job_fields(
    *,
    job_id: str,
    cadence: str,
    coalesce: str,
    command: list[str],
    surface: bool,
    enabled: bool,
    catchup_window: str,
    timeout: str,
) -> JobSpec:
    _check_id(job_id)
    try:
        parse_cadence(cadence)
    except CadenceError as exc:
        raise JobValidationError(f"invalid cadence: {exc}") from exc
    coalesce_kind = _check_coalesce(coalesce)
    command_tuple = _check_command(command)
    _check_positive_duration("catchup_window", catchup_window)
    _check_positive_duration("timeout", timeout)
    return JobSpec(
        job_id=job_id,
        cadence=cadence,
        coalesce=coalesce_kind,
        command=command_tuple,
        surface=surface,
        enabled=enabled,
        catchup_window=catchup_window,
        timeout=timeout,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_jobspec.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/jobspec.py tests/scheduler/test_jobspec.py
git commit -m "feat(scheduler): JobSpec value object + boundary validator

[Cld]"
```

---

### Task 5: `digest.py` — pure digest formatting

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/digest.py`
- Test: `tests/scheduler/test_digest.py`

**Responsibilities:** pure formatting of the SessionStart digest (§11). A `JobReport` value object describes what one job did (id, outcome, owed, ran, deferred, overdue text, consecutive_failures, surface). `format_digest(reports, parse_error=None) -> str` produces:
- `✓ ran <id> (<overdue> overdue)` for a successful run/back-fill,
- `✗ <id> failed (<Nth> consecutive) — see fires.jsonl` for a failure (always shown regardless of `surface`),
- `⏳ <id>: <n> backfills deferred` when capped,
- a `skip_expired` note when instants were dropped,
- an unparseable-registry warning when `parse_error` is set, and runs nothing.
Successful jobs with `surface=False` are omitted. Empty input → empty string.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_digest.py
from __future__ import annotations

from cc_session_tools.lib.scheduler.digest import JobReport, Outcome, format_digest


def _ran(job_id: str, surface: bool = True, overdue: str = "1d") -> JobReport:
    return JobReport(job_id=job_id, outcome=Outcome.RAN, surface=surface,
                     overdue=overdue, ran=1, deferred=0, expired=0,
                     consecutive_failures=0)


def test_empty_reports_is_empty_string() -> None:
    assert format_digest([]) == ""


def test_ran_surfaced_job_appears() -> None:
    out = format_digest([_ran("tesco-shop-check")])
    assert "ran tesco-shop-check" in out
    assert "1d overdue" in out


def test_silent_success_is_omitted() -> None:
    out = format_digest([_ran("quiet-job", surface=False)])
    assert "quiet-job" not in out


def test_failure_always_surfaces_even_when_silent() -> None:
    r = JobReport(job_id="calendar-sync", outcome=Outcome.FAILED, surface=False,
                  overdue="2d", ran=0, deferred=0, expired=0, consecutive_failures=2)
    out = format_digest([r])
    assert "calendar-sync failed" in out
    assert "2nd consecutive" in out
    assert "fires.jsonl" in out


def test_deferred_backfills_reported() -> None:
    r = JobReport(job_id="foo", outcome=Outcome.RAN, surface=True, overdue="",
                  ran=5, deferred=7, expired=0, consecutive_failures=0)
    out = format_digest([r])
    assert "7 backfills deferred" in out


def test_unparseable_registry_warning_runs_nothing() -> None:
    out = format_digest([], parse_error="jobs.toml line 4: invalid TOML")
    assert "jobs.toml failed to parse" in out
    assert "no jobs ran" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_digest.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/digest.py
"""Pure formatting of the SessionStart catch-up digest (§11). Takes structured
sweep results, returns a string. No I/O."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Outcome(str, Enum):
    RAN = "ran"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobReport:
    job_id: str
    outcome: Outcome
    surface: bool
    overdue: str
    ran: int
    deferred: int
    expired: int
    consecutive_failures: int


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _line(report: JobReport) -> str | None:
    if report.outcome is Outcome.FAILED:
        return (
            f"✗ {report.job_id} failed "
            f"({_ordinal(report.consecutive_failures)} consecutive) — see fires.jsonl"
        )
    if not report.surface:
        return None
    overdue = f" ({report.overdue} overdue)" if report.overdue else ""
    base = f"✓ ran {report.job_id}{overdue}"
    if report.deferred:
        base += f"\n⏳ {report.job_id}: {report.deferred} backfills deferred"
    if report.expired:
        base += f"\n   ({report.expired} missed run(s) dropped as expired)"
    return base


def format_digest(reports: list[JobReport], *, parse_error: str | None = None) -> str:
    if parse_error is not None:
        return f"[cc-scheduler] jobs.toml failed to parse — no jobs ran: {parse_error}"
    lines = [line for line in (_line(r) for r in reports) if line is not None]
    if not lines:
        return ""
    return "\n".join(["[cc-scheduler] scheduled-task catch-up:", *lines])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_digest.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/digest.py tests/scheduler/test_digest.py
git commit -m "feat(scheduler): pure digest formatter

[Cld]"
```

---

# Phase B — state and registry I/O

### Task 6: `state.py` — `JobState` + atomic state.json I/O

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/state.py`
- Test: `tests/scheduler/test_state.py`

**Responsibilities:** the per-job state store. Root is `~/.claude/cc-scheduler/` (env-overridable `CC_SCHEDULER_DIR` so tests redirect). `JobState` value object (registered_at / last_success / last_attempt / consecutive_failures, all UTC ISO strings or `None`, plus the int counter). `load_all_state() -> dict[str, JobState]` and `save_all_state(states)` round-trip `state.json` via atomic `.tmp`-swap. `ensure_registered(states, job_id, now)` stamps `registered_at = now` if the job has no state entry (so a hand-added job does not back-fill from epoch — §9.1). All timestamp parse/format helpers live here.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_state.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import state as st

UTC = timezone.utc


def test_scheduler_dir_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    assert st.scheduler_dir() == tmp_path / "sched"


def test_load_missing_state_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert st.load_all_state() == {}


def test_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    states = {
        "j": st.JobState(
            registered_at="2026-06-20T00:00:00Z",
            last_success="2026-06-20T09:00:00Z",
            last_attempt="2026-06-20T09:00:00Z",
            consecutive_failures=0,
        )
    }
    st.save_all_state(states)
    assert not (tmp_path / "state.json.tmp").exists()
    assert st.load_all_state() == states


def test_ensure_registered_stamps_new_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    states: dict[str, st.JobState] = {}
    js = st.ensure_registered(states, "new-job", now)
    assert js.registered_at == "2026-06-22T08:00:00Z"
    assert states["new-job"].registered_at == "2026-06-22T08:00:00Z"


def test_ensure_registered_leaves_existing_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    existing = st.JobState(registered_at="2026-01-01T00:00:00Z", last_success=None,
                           last_attempt=None, consecutive_failures=0)
    states = {"j": existing}
    assert st.ensure_registered(states, "j", now) == existing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_state.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/state.py
"""Per-job state store: registered_at / last_success / last_attempt /
consecutive_failures, persisted to ``<scheduler-dir>/state.json`` via atomic
.tmp-swap. The scheduler dir defaults to ~/.claude/cc-scheduler and is
env-overridable via CC_SCHEDULER_DIR (tests redirect through it)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEDULER_DIR_ENV = "CC_SCHEDULER_DIR"
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def scheduler_dir() -> Path:
    raw = os.environ.get(SCHEDULER_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "cc-scheduler"


def state_path() -> Path:
    return scheduler_dir() / "state.json"


def format_ts(dt: datetime) -> str:
    return dt.astimezone().strftime(_TS_FMT) if dt.tzinfo else dt.strftime(_TS_FMT)


@dataclass(frozen=True, slots=True)
class JobState:
    registered_at: str
    last_success: str | None
    last_attempt: str | None
    consecutive_failures: int


def load_all_state() -> dict[str, JobState]:
    path = state_path()
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    out: dict[str, JobState] = {}
    for job_id, fields in data.items():
        out[job_id] = JobState(
            registered_at=str(fields["registered_at"]),
            last_success=fields.get("last_success"),
            last_attempt=fields.get("last_attempt"),
            consecutive_failures=int(fields.get("consecutive_failures", 0)),
        )
    return out


def save_all_state(states: dict[str, JobState]) -> None:
    target = scheduler_dir()
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        job_id: {
            "registered_at": js.registered_at,
            "last_success": js.last_success,
            "last_attempt": js.last_attempt,
            "consecutive_failures": js.consecutive_failures,
        }
        for job_id, js in states.items()
    }
    path = state_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def ensure_registered(
    states: dict[str, JobState], job_id: str, now: datetime
) -> JobState:
    """Return the job's state, stamping ``registered_at = now`` if absent so a
    hand-added job does not back-fill from epoch (§9.1). Mutates ``states``."""
    if job_id not in states:
        states[job_id] = JobState(
            registered_at=format_ts(now),
            last_success=None,
            last_attempt=None,
            consecutive_failures=0,
        )
    return states[job_id]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/state.py tests/scheduler/test_state.py
git commit -m "feat(scheduler): JobState + atomic state.json I/O

[Cld]"
```

---

### Task 7: `registry.py` — jobs.toml read/write

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/registry.py`
- Test: `tests/scheduler/test_registry.py`

**Responsibilities:** read/write `<scheduler-dir>/jobs.toml`. `load_registry() -> list[JobSpec]` parses with `tomllib`, applies §6 defaults for omitted fields, validates each via `validate_job_fields`, and rejects duplicate ids; a malformed TOML raises `RegistryError`. `add_job(spec)` appends (rejecting a duplicate id), `replace_job(spec)`, `remove_job(job_id)`, `set_enabled(job_id, enabled)` rewrite atomically via `.tmp`-swap. The TOML *writer* is a small hand-rolled serialiser (we control the schema: id/cadence/coalesce strings, a string-array command, three booleans, two duration strings) with a generated-file header line naming the writer. Missing file → empty registry (lazy creation on first write).

> **Reuse note:** the duration/coalesce/cadence validation is already in `jobspec.validate_job_fields`; the registry calls it and never re-implements. Defaults (§6): `coalesce="one"`, `surface=true`, `enabled=true`, `catchup_window="7d"`, `timeout="60s"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_registry.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, validate_job_fields


def _spec(job_id: str = "tesco-shop-check"):
    return validate_job_fields(
        job_id=job_id, cadence="daily@09:00", coalesce="one",
        command=["ccst", "hooks", "run", "check-tesco-due"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
    )


def test_load_missing_registry_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert reg.load_registry() == []


def test_add_then_load_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    loaded = reg.load_registry()
    assert len(loaded) == 1
    assert loaded[0].job_id == "tesco-shop-check"
    assert loaded[0].command == ("ccst", "hooks", "run", "check-tesco-due")
    assert loaded[0].coalesce is CoalesceKind.ONE


def test_add_duplicate_id_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    with pytest.raises(reg.RegistryError):
        reg.add_job(_spec())


def test_defaults_applied_for_omitted_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "jobs.toml").write_text(
        '[[job]]\nid = "minimal"\ncadence = "every:6h"\ncommand = ["echo", "hi"]\n'
    )
    loaded = reg.load_registry()
    assert loaded[0].coalesce is CoalesceKind.ONE
    assert loaded[0].surface is True
    assert loaded[0].enabled is True
    assert loaded[0].catchup_window == "7d"
    assert loaded[0].timeout == "60s"


def test_malformed_toml_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "jobs.toml").write_text("[[job]\nid = broken")
    with pytest.raises(reg.RegistryError):
        reg.load_registry()


def test_remove_and_set_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec("a"))
    reg.add_job(_spec("b"))
    reg.set_enabled("a", False)
    assert {s.job_id: s.enabled for s in reg.load_registry()}["a"] is False
    reg.remove_job("b")
    assert [s.job_id for s in reg.load_registry()] == ["a"]


def test_remove_unknown_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with pytest.raises(reg.RegistryError):
        reg.remove_job("ghost")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_registry.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/registry.py
"""jobs.toml registry I/O. Reads with stdlib tomllib; writes with a small,
schema-specific serialiser (the registry shape is fully controlled here). Each
record is validated through jobspec.validate_job_fields on load; duplicate ids
and malformed TOML raise RegistryError."""
from __future__ import annotations

import tomllib
from pathlib import Path

from cc_session_tools.lib.scheduler.jobspec import (
    JobSpec,
    JobValidationError,
    validate_job_fields,
)
from cc_session_tools.lib.scheduler.state import scheduler_dir

_GENERATED_HEADER = (
    "# cc-scheduler job registry. Hand-editable; also written by `ccsched`.\n"
    "# Serialised by cc_session_tools.lib.scheduler.registry.\n"
)
_DEFAULTS = {
    "coalesce": "one",
    "surface": True,
    "enabled": True,
    "catchup_window": "7d",
    "timeout": "60s",
}


class RegistryError(ValueError):
    """Raised for unparseable jobs.toml, duplicate ids, or unknown-id mutations."""


def registry_path() -> Path:
    return scheduler_dir() / "jobs.toml"


def _spec_from_table(table: dict[str, object]) -> JobSpec:
    try:
        return validate_job_fields(
            job_id=str(table["id"]),
            cadence=str(table["cadence"]),
            coalesce=str(table.get("coalesce", _DEFAULTS["coalesce"])),
            command=[str(x) for x in table["command"]],  # type: ignore[union-attr]
            surface=bool(table.get("surface", _DEFAULTS["surface"])),
            enabled=bool(table.get("enabled", _DEFAULTS["enabled"])),
            catchup_window=str(table.get("catchup_window", _DEFAULTS["catchup_window"])),
            timeout=str(table.get("timeout", _DEFAULTS["timeout"])),
        )
    except KeyError as exc:
        raise RegistryError(f"job table missing required field: {exc}") from exc
    except JobValidationError as exc:
        raise RegistryError(f"invalid job in jobs.toml: {exc}") from exc


def load_registry() -> list[JobSpec]:
    path = registry_path()
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"jobs.toml is not valid TOML: {exc}") from exc
    specs: list[JobSpec] = []
    seen: set[str] = set()
    for table in data.get("job", []):
        spec = _spec_from_table(table)
        if spec.job_id in seen:
            raise RegistryError(f"duplicate job id in jobs.toml: {spec.job_id!r}")
        seen.add(spec.job_id)
        specs.append(spec)
    return specs


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _serialise(specs: list[JobSpec]) -> str:
    blocks: list[str] = [_GENERATED_HEADER]
    for s in specs:
        cmd = ", ".join(_toml_str(part) for part in s.command)
        blocks.append(
            "[[job]]\n"
            f"id = {_toml_str(s.job_id)}\n"
            f"cadence = {_toml_str(s.cadence)}\n"
            f"coalesce = {_toml_str(s.coalesce.value)}\n"
            f"command = [{cmd}]\n"
            f"surface = {str(s.surface).lower()}\n"
            f"enabled = {str(s.enabled).lower()}\n"
            f"catchup_window = {_toml_str(s.catchup_window)}\n"
            f"timeout = {_toml_str(s.timeout)}\n"
        )
    return "\n".join(blocks)


def _write(specs: list[JobSpec]) -> None:
    target = scheduler_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = registry_path()
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(_serialise(specs))
    tmp.replace(path)


def add_job(spec: JobSpec) -> None:
    specs = load_registry()
    if any(s.job_id == spec.job_id for s in specs):
        raise RegistryError(f"job id already exists: {spec.job_id!r}")
    specs.append(spec)
    _write(specs)


def replace_job(spec: JobSpec) -> None:
    specs = load_registry()
    if not any(s.job_id == spec.job_id for s in specs):
        raise RegistryError(f"unknown job id: {spec.job_id!r}")
    _write([spec if s.job_id == spec.job_id else s for s in specs])


def remove_job(job_id: str) -> None:
    specs = load_registry()
    kept = [s for s in specs if s.job_id != job_id]
    if len(kept) == len(specs):
        raise RegistryError(f"unknown job id: {job_id!r}")
    _write(kept)


def set_enabled(job_id: str, enabled: bool) -> None:
    specs = load_registry()
    found = False
    new: list[JobSpec] = []
    for s in specs:
        if s.job_id == job_id:
            found = True
            new.append(
                JobSpec(
                    job_id=s.job_id, cadence=s.cadence, coalesce=s.coalesce,
                    command=s.command, surface=s.surface, enabled=enabled,
                    catchup_window=s.catchup_window, timeout=s.timeout,
                )
            )
        else:
            new.append(s)
    if not found:
        raise RegistryError(f"unknown job id: {job_id!r}")
    _write(new)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/registry.py tests/scheduler/test_registry.py
git commit -m "feat(scheduler): jobs.toml registry read/write with defaults + validation

[Cld]"
```

---

### Task 8: `lock.py` — `O_EXCL` sweep lock with stale reclamation

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/lock.py`
- Test: `tests/scheduler/test_lock.py`

**Responsibilities:** `sweep_lock()` context manager creating `<scheduler-dir>/.sweep.lock` via `os.open(O_CREAT|O_EXCL)`. The lock file stores `{"pid": ..., "started": ...}`. On `EEXIST`, read the holder pid; if the process is dead (`os.kill(pid, 0)` raises `ProcessLookupError`), reclaim the lock; otherwise raise `SweepLockHeld`. The winner removes the lock on exit. Includes a race test (threads) asserting exactly one winner, and a stale-lock test (write a lock with a dead pid → next acquire succeeds).

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_lock.py
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler.lock import SweepLockHeld, sweep_lock


def test_acquire_then_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with sweep_lock():
        pass
    with sweep_lock():  # released, so re-acquire works
        pass


def test_second_concurrent_acquire_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with sweep_lock():
        with pytest.raises(SweepLockHeld):
            with sweep_lock():
                pass


def test_stale_lock_is_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    # A lock owned by a pid that does not exist (very high pid).
    (tmp_path / ".sweep.lock").write_text(json.dumps({"pid": 2_000_000_000, "started": "x"}))
    with sweep_lock():  # should reclaim and succeed
        pass


def test_race_has_exactly_one_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    winners = 0
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        nonlocal winners
        barrier.wait()
        try:
            with sweep_lock():
                with guard:
                    winners += 1
                import time
                time.sleep(0.02)
        except SweepLockHeld:
            return

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert winners == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_lock.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/lock.py
"""Single-holder sweep lock at <scheduler-dir>/.sweep.lock.

Atomicity from os.open(O_CREAT|O_EXCL): exactly one caller creates the file.
A contender raises SweepLockHeld unless the recorded holder pid is dead, in
which case the stale lock is reclaimed (§10)."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib.scheduler.state import scheduler_dir


class SweepLockHeld(RuntimeError):
    """Raised when the sweep lock is held by a live process."""


def _lock_path() -> Path:
    return scheduler_dir() / ".sweep.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours to signal
    return True


def _try_create(path: Path) -> int:
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    payload = json.dumps(
        {"pid": os.getpid(), "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    )
    os.write(fd, payload.encode())
    return fd


@contextmanager
def sweep_lock() -> Iterator[None]:
    scheduler_dir().mkdir(parents=True, exist_ok=True)
    path = _lock_path()
    try:
        fd = _try_create(path)
    except FileExistsError:
        holder = _read_holder(path)
        if holder is not None and _pid_alive(holder):
            raise SweepLockHeld(f"sweep lock held by live pid {holder}")
        path.unlink(missing_ok=True)  # stale → reclaim
        fd = _try_create(path)
    try:
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def _read_holder(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pid = data.get("pid")
    return int(pid) if isinstance(pid, int) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_lock.py -q`
Expected: PASS (including the 8-thread race with exactly one winner and the stale-reclaim case).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/lock.py tests/scheduler/test_lock.py
git commit -m "feat(scheduler): O_EXCL sweep lock with stale-lock reclamation

[Cld]"
```

---

### Task 9: `runner.py` + `ledger.py` — subprocess runner and telemetry adapter

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/runner.py`
- Create: `src/cc_session_tools/lib/scheduler/ledger.py`
- Test: `tests/scheduler/test_runner.py`, `tests/scheduler/test_ledger.py`

**Responsibilities:**
- `runner.run_command(argv, timeout) -> RunOutcome` runs the argv via `subprocess.run` with the timeout, capturing stdout/stderr and wall duration; on `TimeoutExpired` returns a timed-out outcome (exit code `None`, `timed_out=True`) without raising. `RunOutcome` is a frozen value object (exit_code, stdout, stderr, duration_ms, timed_out).
- `ledger.record(event)` maps a scheduler event to a `TelemetryEntry` (`hook="catchup"`, `decision="annotate"`, `cache="none"`, `verdict` = a compact JSON of `{job_id, event, owed, ran, exit_code, duration_ms, error}`) and calls `telemetry.log_event` (reuse, not reinvent). `ledger.read_recent(job_id=None, limit=...) -> list[dict]` reads `fires.jsonl`, filters `hook=="catchup"` (and optional job_id), for `ccsched status`. The hooks dir is env-overridable via `CCCS_HOOKS_DIR` (already supported by `telemetry.log_event`'s `hooks_dir` param).

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_runner.py
from __future__ import annotations

import sys
from datetime import timedelta

from cc_session_tools.lib.scheduler.runner import run_command


def test_success_captures_stdout() -> None:
    out = run_command([sys.executable, "-c", "print('hello')"], timeout=timedelta(seconds=10))
    assert out.exit_code == 0
    assert "hello" in out.stdout
    assert out.timed_out is False


def test_non_zero_exit_is_reported_not_raised() -> None:
    out = run_command([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=timedelta(seconds=10))
    assert out.exit_code == 3
    assert out.timed_out is False


def test_timeout_is_reported_not_raised() -> None:
    out = run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=timedelta(milliseconds=200))
    assert out.timed_out is True
    assert out.exit_code is None
```

```python
# tests/scheduler/test_ledger.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import ledger
from cc_session_tools.lib.scheduler.digest import Outcome


def test_record_then_read_recent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    ledger.record(ledger.LedgerEntry(
        job_id="tesco", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
        exit_code=0, duration_ms=42, error=None,
    ))
    rows = ledger.read_recent(job_id="tesco")
    assert len(rows) == 1
    assert rows[0]["job_id"] == "tesco"
    assert rows[0]["event"] == "run"


def test_read_recent_filters_other_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "fires.jsonl").write_text('{"hook":"bash-security-review","verdict":"safe"}\n')
    ledger.record(ledger.LedgerEntry(
        job_id="cal", event=ledger.LedgerEvent.FAIL, owed=1, ran=0,
        exit_code=2, duration_ms=10, error="boom",
    ))
    rows = ledger.read_recent()
    assert all(r["hook"] == "catchup" for r in rows)
    assert rows[0]["job_id"] == "cal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_runner.py tests/scheduler/test_ledger.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/runner.py
"""Per-job subprocess runner: run an argv with a hard timeout, capturing
output and wall duration. Never raises on a non-zero exit or a timeout — the
sweep decides what to record."""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class RunOutcome:
    exit_code: int | None  # None when timed out
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


def run_command(argv: tuple[str, ...] | list[str], timeout: timedelta) -> RunOutcome:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout.total_seconds(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return RunOutcome(
            exit_code=None,
            stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr or "" if isinstance(exc.stderr, str) else "",
            duration_ms=elapsed,
            timed_out=True,
        )
    elapsed = int((time.monotonic() - start) * 1000)
    return RunOutcome(
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_ms=elapsed,
        timed_out=False,
    )
```

```python
# src/cc_session_tools/lib/scheduler/ledger.py
"""Adapter over cccs_hooks.telemetry: write one fires.jsonl line per sweep
action (hook='catchup'), and read recent catchup lines back for `ccsched
status`. Reuses the shared telemetry ledger; does not create a new stream."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from cccs_hooks.telemetry import TelemetryEntry, log_event


class LedgerEvent(str, Enum):
    RUN = "run"
    BACKFILL = "backfill"
    SKIP_EXPIRED = "skip_expired"
    DEFER = "defer"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    job_id: str
    event: LedgerEvent
    owed: int
    ran: int
    exit_code: int | None
    duration_ms: int
    error: str | None


def _hooks_dir() -> Path | None:
    raw = os.environ.get("CCCS_HOOKS_DIR")
    return Path(raw) if raw else None


def record(entry: LedgerEntry) -> None:
    verdict = json.dumps(
        {
            "job_id": entry.job_id,
            "event": entry.event.value,
            "owed": entry.owed,
            "ran": entry.ran,
            "exit_code": entry.exit_code,
            "duration_ms": entry.duration_ms,
            "error": entry.error,
        },
        separators=(",", ":"),
    )
    log_event(
        TelemetryEntry(
            hook="catchup",
            event="",
            tool="",
            session_id="",
            cwd_short="",
            decision="annotate",
            cache="none",
            verdict=verdict,
            input_hash="",
        ),
        hooks_dir=_hooks_dir(),
    )


def read_recent(job_id: str | None = None, *, limit: int = 50) -> list[dict[str, object]]:
    hooks_dir = _hooks_dir() or (Path.home() / ".claude" / "hooks")
    fires = hooks_dir / "fires.jsonl"
    if not fires.is_file():
        return []
    rows: list[dict[str, object]] = []
    for raw in fires.read_text().splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if line.get("hook") != "catchup":
            continue
        try:
            detail = json.loads(line.get("verdict", "{}"))
        except json.JSONDecodeError:
            detail = {}
        merged = {"ts": line.get("ts"), "hook": "catchup", **detail}
        if job_id is not None and merged.get("job_id") != job_id:
            continue
        rows.append(merged)
    return rows[-limit:]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_runner.py tests/scheduler/test_ledger.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/runner.py src/cc_session_tools/lib/scheduler/ledger.py tests/scheduler/test_runner.py tests/scheduler/test_ledger.py
git commit -m "feat(scheduler): subprocess runner + telemetry-ledger adapter

[Cld]"
```

---

# Phase C — sweep orchestration

### Task 10: `sweep.py` — the reconcile sweep

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/sweep.py`
- Test: `tests/scheduler/test_sweep.py`

**Responsibilities:** compose the lib into the reconcile algorithm (§9). `run_sweep(*, now, budget, per_sweep_cap, deadline_clock) -> SweepResult` does, holding the sweep lock for the whole run:
1. Load the registry. On `RegistryError`, return a `SweepResult` carrying `parse_error` and no reports (the hook turns this into the warning digest).
2. Load state once. For each **enabled** job in registry order, until the time budget (default 10s) is exhausted:
   - `ensure_registered` (stamps `registered_at = now` for never-seen jobs — §9.1).
   - `baseline = last_success or registered_at`; compute `owed(...)`.
   - Nothing owed → record nothing, continue.
   - `coalesce: one` and owed ≥ 1 → run once; on success advance `last_success = now`.
   - `coalesce: each` → run up to `per_sweep_cap` (default 5) owed instants oldest-first; advance `last_success` to the last satisfied instant; record `DEFER` for the remainder.
   - On success: reset `consecutive_failures`, append `RUN`/`BACKFILL` ledger events, set `last_attempt`, build a `JobReport`.
   - On failure (non-zero or timed out): append `FAIL`, do **not** advance `last_success`, increment `consecutive_failures`, set `last_attempt`, build a failing `JobReport`. At most one attempt per job per sweep.
   - Expired instants → `SKIP_EXPIRED` ledger event (visible, not silent).
3. Time-box: when the budget is exhausted, remaining jobs are left untouched (state unchanged → picked up next sweep). Save state once at the end.
4. Return `SweepResult(reports=..., parse_error=None)`.

If the lock is held (`SweepLockHeld`), `run_sweep` returns an empty `SweepResult` (the other session is sweeping).

> **Testability:** inject `now: datetime` and a `monotonic` clock callable (default `time.monotonic`) so the budget is testable. The runner is injected too (default `runner.run_command`) so tests stub job execution without spawning processes. Keep the lock real (it is fast and the lock tests already cover it), but allow the test to set `CC_SCHEDULER_DIR`/`CCCS_HOOKS_DIR` to tmp paths.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_sweep.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import ledger as ld
from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import state as st
from cc_session_tools.lib.scheduler import sweep as sw
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields
from cc_session_tools.lib.scheduler.runner import RunOutcome

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))


def _add(job_id: str, cadence: str = "daily@09:00", coalesce: str = "one") -> None:
    reg.add_job(validate_job_fields(
        job_id=job_id, cadence=cadence, coalesce=coalesce, command=["true"],
        surface=True, enabled=True, catchup_window="30d", timeout="5s",
    ))


def _ok_runner(argv, timeout) -> RunOutcome:
    return RunOutcome(exit_code=0, stdout="", stderr="", duration_ms=1, timed_out=False)


def _fail_runner(argv, timeout) -> RunOutcome:
    return RunOutcome(exit_code=1, stdout="", stderr="boom", duration_ms=1, timed_out=False)


def test_coalesce_one_runs_once_and_advances(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    # Stamp registered_at three days ago so the daily job is overdue.
    states = {"tesco": st.JobState(registered_at="2026-06-17T09:00:00Z",
                                   last_success=None, last_attempt=None,
                                   consecutive_failures=0)}
    st.save_all_state(states)
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = sw.run_sweep(now=now, runner=_ok_runner)
    assert any(r.job_id == "tesco" and r.ran == 1 for r in result.reports)
    after = st.load_all_state()["tesco"]
    assert after.last_success is not None
    assert after.consecutive_failures == 0
    # Several owed instants (18th, 19th, 20th) coalesced to a single run, so the ledger
    # event must be BACKFILL, not RUN. This pins the owed>1 RUN-vs-BACKFILL boundary.
    rows = ld.read_recent(job_id="tesco")
    assert rows[-1]["event"] == ld.LedgerEvent.BACKFILL.value


def test_failure_does_not_advance_and_increments(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("cal")
    st.save_all_state({"cal": st.JobState(registered_at="2026-06-17T09:00:00Z",
                                          last_success=None, last_attempt=None,
                                          consecutive_failures=0)})
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = sw.run_sweep(now=now, runner=_fail_runner)
    rep = next(r for r in result.reports if r.job_id == "cal")
    assert rep.outcome.value == "failed"
    after = st.load_all_state()["cal"]
    assert after.last_success is None
    assert after.consecutive_failures == 1


def test_coalesce_each_caps_and_defers(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("each-job", cadence="every:1h", coalesce="each")
    st.save_all_state({"each-job": st.JobState(registered_at="2026-06-20T00:00:00Z",
                                               last_success=None, last_attempt=None,
                                               consecutive_failures=0)})
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)  # 12 hourly instants owed
    result = sw.run_sweep(now=now, runner=_ok_runner, per_sweep_cap=5)
    rep = next(r for r in result.reports if r.job_id == "each-job")
    assert rep.ran == 5
    assert rep.deferred == 7


def test_disabled_job_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("on")
    reg.set_enabled("on", False)
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = sw.run_sweep(now=now, runner=_ok_runner)
    assert all(r.job_id != "on" for r in result.reports)


def test_parse_error_surfaces_and_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    st.scheduler_dir().mkdir(parents=True, exist_ok=True)
    reg.registry_path().write_text("[[job]\nbroken")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = sw.run_sweep(now=now, runner=_ok_runner)
    assert result.parse_error is not None
    assert result.reports == []


def test_budget_exhaustion_leaves_remaining_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("a")
    _add("b")
    for jid in ("a", "b"):
        st.save_all_state({**st.load_all_state(), jid: st.JobState(
            registered_at="2026-06-17T09:00:00Z", last_success=None,
            last_attempt=None, consecutive_failures=0)})
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    # A clock that reports the budget exhausted immediately after the first job.
    ticks = iter([0.0, 0.0, 999.0, 999.0, 999.0])
    result = sw.run_sweep(now=now, runner=_ok_runner,
                          budget=timedelta(seconds=10), clock=lambda: next(ticks))
    ran_ids = {r.job_id for r in result.reports}
    assert "a" in ran_ids and "b" not in ran_ids
    # b's state is unchanged (no last_attempt), so it is picked up next sweep.
    assert st.load_all_state()["b"].last_attempt is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_sweep.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/scheduler/sweep.py
"""Reconcile sweep (§9): for each enabled job, back-fill what is owed since
its last success, respecting coalescing, the time budget, and the per-sweep
cap. Pure logic is delegated to due/jobspec/digest; this module composes the
I/O (registry, state, lock, runner, ledger). The hook is a thin wrapper.

``now`` and the monotonic ``clock`` are injected so the time budget is testable
without sleeping; the ``runner`` is injected so tests stub execution."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from cc_session_tools.lib.scheduler import ledger, registry, state
from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.digest import JobReport, Outcome
from cc_session_tools.lib.scheduler.duration import parse_duration
from cc_session_tools.lib.scheduler.due import owed
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, JobSpec
from cc_session_tools.lib.scheduler.ledger import LedgerEntry, LedgerEvent
from cc_session_tools.lib.scheduler.lock import SweepLockHeld, sweep_lock
from cc_session_tools.lib.scheduler.runner import RunOutcome, run_command
from cc_session_tools.lib.scheduler.state import JobState

logger = logging.getLogger(__name__)

_DEFAULT_BUDGET = timedelta(seconds=10)
_DEFAULT_CAP = 5

Runner = Callable[[tuple[str, ...], timedelta], RunOutcome]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class SweepResult:
    reports: list[JobReport]
    parse_error: str | None


def _overdue_text(baseline: datetime, now: datetime) -> str:
    delta = now - baseline
    days = delta.days
    if days >= 1:
        return f"{days}d"
    hours = delta.seconds // 3600
    return f"{hours}h" if hours else f"{delta.seconds // 60}m"


def _parse_ts(value: str) -> datetime:
    from datetime import timezone
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _run_one(
    spec: JobSpec, js: JobState, now: datetime, cap: int, runner: Runner
) -> tuple[JobState, JobReport | None]:
    cadence = parse_cadence(spec.cadence)
    window = parse_duration(spec.catchup_window)
    timeout = parse_duration(spec.timeout)
    baseline = _parse_ts(js.last_success or js.registered_at)
    result = owed(cadence, baseline, now, catchup_window=window)

    if result.expired_count:
        ledger.record(LedgerEntry(
            job_id=spec.job_id, event=LedgerEvent.SKIP_EXPIRED,
            owed=result.expired_count, ran=0, exit_code=None, duration_ms=0,
            error=None,
        ))
    if not result.instants:
        return js, None

    runs = len(result.instants) if spec.coalesce is CoalesceKind.EACH else 1
    runs = min(runs, cap if spec.coalesce is CoalesceKind.EACH else runs)
    deferred = (len(result.instants) - runs) if spec.coalesce is CoalesceKind.EACH else 0

    outcome: RunOutcome = runner(spec.command, timeout)
    attempt_ts = state.format_ts(now)
    failed = outcome.timed_out or (outcome.exit_code not in (0, None) and not outcome.timed_out) or outcome.exit_code != 0

    if failed:
        new_state = JobState(
            registered_at=js.registered_at, last_success=js.last_success,
            last_attempt=attempt_ts, consecutive_failures=js.consecutive_failures + 1,
        )
        ledger.record(LedgerEntry(
            job_id=spec.job_id, event=LedgerEvent.FAIL, owed=len(result.instants),
            ran=0, exit_code=outcome.exit_code, duration_ms=outcome.duration_ms,
            error=outcome.stderr.strip()[:200] or ("timed out" if outcome.timed_out else None),
        ))
        report = JobReport(
            job_id=spec.job_id, outcome=Outcome.FAILED, surface=spec.surface,
            overdue=_overdue_text(baseline, now), ran=0, deferred=0,
            expired=result.expired_count, consecutive_failures=new_state.consecutive_failures,
        )
        return new_state, report

    if spec.coalesce is CoalesceKind.ONE:
        new_success = state.format_ts(now)
    else:
        new_success = state.format_ts(result.instants[runs - 1])
    new_state = JobState(
        registered_at=js.registered_at, last_success=new_success,
        last_attempt=attempt_ts, consecutive_failures=0,
    )
    event = LedgerEvent.RUN if result.expired_count == 0 and runs == 1 else LedgerEvent.BACKFILL
    ledger.record(LedgerEntry(
        job_id=spec.job_id, event=event, owed=len(result.instants), ran=runs,
        exit_code=outcome.exit_code, duration_ms=outcome.duration_ms, error=None,
    ))
    if deferred:
        ledger.record(LedgerEntry(
            job_id=spec.job_id, event=LedgerEvent.DEFER, owed=len(result.instants),
            ran=runs, exit_code=None, duration_ms=0, error=None,
        ))
    report = JobReport(
        job_id=spec.job_id, outcome=Outcome.RAN, surface=spec.surface,
        overdue=_overdue_text(baseline, now), ran=runs, deferred=deferred,
        expired=result.expired_count, consecutive_failures=0,
    )
    return new_state, report


def run_sweep(
    *,
    now: datetime,
    budget: timedelta = _DEFAULT_BUDGET,
    per_sweep_cap: int = _DEFAULT_CAP,
    runner: Runner = run_command,
    clock: Clock = time.monotonic,
) -> SweepResult:
    try:
        with sweep_lock():
            try:
                specs = registry.load_registry()
            except registry.RegistryError as exc:
                return SweepResult(reports=[], parse_error=str(exc))

            states = state.load_all_state()
            reports: list[JobReport] = []
            start = clock()
            for spec in specs:
                if not spec.enabled:
                    continue
                if clock() - start >= budget.total_seconds():
                    logger.info("sweep budget exhausted; %s and later deferred", spec.job_id)
                    break
                js = state.ensure_registered(states, spec.job_id, now)
                new_state, report = _run_one(spec, js, now, per_sweep_cap, runner)
                states[spec.job_id] = new_state
                if report is not None:
                    reports.append(report)
            state.save_all_state(states)
            return SweepResult(reports=reports, parse_error=None)
    except SweepLockHeld:
        logger.info("sweep already running in another session; skipping")
        return SweepResult(reports=[], parse_error=None)
```

> **Implementer note:** simplify the `failed` predicate to `failed = outcome.timed_out or outcome.exit_code != 0` once you confirm `exit_code is None` only ever co-occurs with `timed_out is True` (the runner guarantees this). The verbose form above is a placeholder — replace it with the clean one and keep the test green. Do not leave dead boolean clauses.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_sweep.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/sweep.py tests/scheduler/test_sweep.py
git commit -m "feat(scheduler): reconcile sweep orchestration (coalesce, budget, cap)

[Cld]"
```

---

# Phase D — CLI

### Task 11: `ccsched` CLI — all subcommands

**Files:**
- Create: `src/cc_session_tools/cli/ccsched.py`
- Test: `tests/scheduler/test_ccsched_cli.py`

**Responsibilities:** the management surface (§12), a thin argparse layer matching `ccd.py`/`ccst.py` conventions (`_build_parser()`, `main(argv=None) -> int`, `--version`). Subcommands:
- `add` — flags `--id`, `--cadence`, `--coalesce` (default one), `--command …` (REMAINDER argv), `--surface/--no-surface`, `--catchup-window` (default 7d), `--timeout` (default 60s); validates via `jobspec.validate_job_fields`, then `registry.add_job`. Boundary validation errors → exit 2; duplicate id (`RegistryError`) → exit 2.
- `list` — table: id, cadence, coalesce, enabled, last_success, next_due (computed on the fly from cadence + `last_success or registered_at`, reusing `due.next_due`).
- `edit <id>` — modify provided fields, rebuild + `replace_job`.
- `enable <id>` / `disable <id>` — `set_enabled`.
- `remove <id>` — `remove_job`.
- `run <id> [--force]` — run one job now via `runner.run_command`, record to ledger; `--force` runs even if nothing is owed.
- `status [<id>]` — recent ledger entries via `ledger.read_recent`.
- `sweep` — call `sweep.run_sweep(now=datetime.now(...))`, print the digest.

Unknown id on `edit/enable/disable/remove/run` → exit 2 with a clear message.

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_ccsched_cli.py
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run(args: list[str], sched_dir: Path, hooks_dir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CC_SCHEDULER_DIR"] = str(sched_dir)
    env["CCCS_HOOKS_DIR"] = str(hooks_dir)
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", *args],
        capture_output=True, text=True, env=env,
    )


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "sched", tmp_path / "hooks"


def _add_ok(tmp_path: Path, job_id: str = "tesco") -> subprocess.CompletedProcess[str]:
    sched, hooks = _dirs(tmp_path)
    return _run(
        ["add", "--id", job_id, "--cadence", "daily@09:00",
         "--catchup-window", "7d", "--timeout", "5s",
         "--command", "true"],
        sched, hooks,
    )


def test_add_happy_path(tmp_path: Path) -> None:
    res = _add_ok(tmp_path)
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "sched" / "jobs.toml").is_file()


def test_add_rejects_bad_cadence(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["add", "--id", "j", "--cadence", "hourly", "--command", "true"], sched, hooks)
    assert res.returncode == 2
    assert "cadence" in (res.stderr + res.stdout).lower()


def test_add_rejects_duplicate_id(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    res = _add_ok(tmp_path)
    assert res.returncode == 2
    assert "already exists" in (res.stderr + res.stdout).lower()


def test_add_rejects_empty_command(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["add", "--id", "j", "--cadence", "daily@09:00", "--command"], sched, hooks)
    assert res.returncode != 0


def test_add_rejects_bad_coalesce(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["add", "--id", "j", "--cadence", "daily@09:00",
                "--coalesce", "sometimes", "--command", "true"], sched, hooks)
    assert res.returncode == 2


def test_list_shows_next_due(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["list"], sched, hooks)
    assert res.returncode == 0
    assert "tesco" in res.stdout
    assert "next_due" in res.stdout.lower() or "next" in res.stdout.lower()


def test_disable_then_enable(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["disable", "tesco"], sched, hooks).returncode == 0
    assert _run(["enable", "tesco"], sched, hooks).returncode == 0


def test_enable_unknown_id_errors(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["enable", "ghost"], sched, hooks)
    assert res.returncode == 2


def test_remove(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["remove", "tesco"], sched, hooks).returncode == 0
    assert "tesco" not in _run(["list"], sched, hooks).stdout


def test_run_force_records_ledger(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["run", "tesco", "--force"], sched, hooks)
    assert res.returncode == 0
    assert (hooks / "fires.jsonl").is_file()


def test_status_empty_ok(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["status"], sched, hooks).returncode == 0


def test_sweep_runs(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["sweep"], sched, hooks).returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_ccsched_cli.py -q`
Expected: FAIL (module `cc_session_tools.cli.ccsched` not found).

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/cli/ccsched.py
"""ccsched — manage local recurring jobs reconciled on Claude Code session
start. Thin argparse layer; validation lives at this boundary, the scheduler
lib trusts validated input."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from cc_session_tools import __version__
from cc_session_tools.lib.scheduler import ledger, registry, state, sweep
from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.digest import format_digest
from cc_session_tools.lib.scheduler.due import next_due
from cc_session_tools.lib.scheduler.duration import parse_duration
from cc_session_tools.lib.scheduler.jobspec import (
    JobSpec,
    JobValidationError,
    validate_job_fields,
)
from cc_session_tools.lib.scheduler.runner import run_command


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccsched",
        description="Manage local recurring jobs reconciled on session start.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    add_p = sub.add_parser("add", help="Register a job.")
    add_p.add_argument("--id", required=True)
    add_p.add_argument("--cadence", required=True)
    add_p.add_argument("--coalesce", default="one")
    add_p.add_argument("--catchup-window", default="7d")
    add_p.add_argument("--timeout", default="60s")
    surface = add_p.add_mutually_exclusive_group()
    surface.add_argument("--surface", dest="surface", action="store_true", default=True)
    surface.add_argument("--no-surface", dest="surface", action="store_false")
    add_p.add_argument("--command", nargs=argparse.REMAINDER, default=[],
                       help="The argv to run (everything after --command).")

    list_p = sub.add_parser("list", help="List jobs with next_due.")  # noqa: F841

    edit_p = sub.add_parser("edit", help="Modify an existing job.")
    edit_p.add_argument("id")
    edit_p.add_argument("--cadence")
    edit_p.add_argument("--coalesce")
    edit_p.add_argument("--catchup-window")
    edit_p.add_argument("--timeout")
    esurface = edit_p.add_mutually_exclusive_group()
    esurface.add_argument("--surface", dest="surface", action="store_true", default=None)
    esurface.add_argument("--no-surface", dest="surface", action="store_false", default=None)
    edit_p.add_argument("--command", nargs=argparse.REMAINDER, default=None)

    for verb in ("enable", "disable", "remove"):
        sp = sub.add_parser(verb, help=f"{verb.capitalize()} a job.")
        sp.add_argument("id")

    run_p = sub.add_parser("run", help="Run one job now.")
    run_p.add_argument("id")
    run_p.add_argument("--force", action="store_true")

    status_p = sub.add_parser("status", help="Recent ledger entries.")
    status_p.add_argument("id", nargs="?", default=None)

    sub.add_parser("sweep", help="Run the reconcile sweep now.")
    return p


def _err(msg: str) -> int:
    print(f"ccsched: {msg}", file=sys.stderr)
    return 2


def _cmd_add(args: argparse.Namespace) -> int:
    try:
        spec = validate_job_fields(
            job_id=args.id, cadence=args.cadence, coalesce=args.coalesce,
            command=list(args.command), surface=args.surface, enabled=True,
            catchup_window=args.catchup_window, timeout=args.timeout,
        )
    except JobValidationError as exc:
        return _err(str(exc))
    try:
        registry.add_job(spec)
    except registry.RegistryError as exc:
        return _err(str(exc))
    print(f"added {spec.job_id}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    specs = registry.load_registry()
    states = state.load_all_state()
    now = datetime.now(timezone.utc)
    print(f"{'id':<24} {'cadence':<18} {'coalesce':<8} {'enabled':<7} {'last_success':<22} next_due")
    for s in specs:
        js = states.get(s.job_id)
        baseline = state._parse_ts_or_none(js.last_success or js.registered_at) if js else now  # noqa: SLF001
        last = (js.last_success if js else None) or "-"
        nd = next_due(parse_cadence(s.cadence), baseline, now).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"{s.job_id:<24} {s.cadence:<18} {s.coalesce.value:<8} "
              f"{str(s.enabled).lower():<7} {last:<22} {nd}")
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    specs = {s.job_id: s for s in registry.load_registry()}
    cur = specs.get(args.id)
    if cur is None:
        return _err(f"unknown job id: {args.id!r}")
    try:
        spec = validate_job_fields(
            job_id=args.id,
            cadence=args.cadence or cur.cadence,
            coalesce=(args.coalesce or cur.coalesce.value),
            command=(args.command if args.command is not None else list(cur.command)),
            surface=cur.surface if args.surface is None else args.surface,
            enabled=cur.enabled,
            catchup_window=args.catchup_window or cur.catchup_window,
            timeout=args.timeout or cur.timeout,
        )
    except JobValidationError as exc:
        return _err(str(exc))
    registry.replace_job(spec)
    print(f"updated {spec.job_id}")
    return 0


def _cmd_set_enabled(job_id: str, enabled: bool) -> int:
    try:
        registry.set_enabled(job_id, enabled)
    except registry.RegistryError as exc:
        return _err(str(exc))
    print(f"{'enabled' if enabled else 'disabled'} {job_id}")
    return 0


def _cmd_remove(job_id: str) -> int:
    try:
        registry.remove_job(job_id)
    except registry.RegistryError as exc:
        return _err(str(exc))
    print(f"removed {job_id}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    specs = {s.job_id: s for s in registry.load_registry()}
    spec: JobSpec | None = specs.get(args.id)
    if spec is None:
        return _err(f"unknown job id: {args.id!r}")
    outcome = run_command(spec.command, parse_duration(spec.timeout))
    now = datetime.now(timezone.utc)
    states = state.load_all_state()
    js = state.ensure_registered(states, spec.job_id, now)
    failed = outcome.timed_out or outcome.exit_code != 0
    states[spec.job_id] = state.JobState(
        registered_at=js.registered_at,
        last_success=js.last_success if failed else state.format_ts(now),
        last_attempt=state.format_ts(now),
        consecutive_failures=js.consecutive_failures + 1 if failed else 0,
    )
    state.save_all_state(states)
    ledger.record(ledger.LedgerEntry(
        job_id=spec.job_id,
        event=ledger.LedgerEvent.FAIL if failed else ledger.LedgerEvent.RUN,
        owed=1, ran=0 if failed else 1, exit_code=outcome.exit_code,
        duration_ms=outcome.duration_ms,
        error=(outcome.stderr.strip()[:200] or None) if failed else None,
    ))
    print(f"{'failed' if failed else 'ran'} {spec.job_id} (exit={outcome.exit_code})")
    return 1 if failed else 0


def _cmd_status(args: argparse.Namespace) -> int:
    rows = ledger.read_recent(job_id=args.id)
    if not rows:
        print("no recent catch-up activity")
        return 0
    for r in rows:
        print(f"{r.get('ts','')} {r.get('job_id',''):<24} {r.get('event',''):<12} "
              f"ran={r.get('ran')} exit={r.get('exit_code')}")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    result = sweep.run_sweep(now=datetime.now(timezone.utc))
    digest = format_digest(result.reports, parse_error=result.parse_error)
    print(digest or "nothing owed")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "add":
        return _cmd_add(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "edit":
        return _cmd_edit(args)
    if args.command == "enable":
        return _cmd_set_enabled(args.id, True)
    if args.command == "disable":
        return _cmd_set_enabled(args.id, False)
    if args.command == "remove":
        return _cmd_remove(args.id)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "sweep":
        return _cmd_sweep(args)
    _build_parser().print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

> **Implementer note:** `_cmd_list` references `state._parse_ts_or_none`. Add a small public helper `parse_ts_or_none(value: str | None) -> datetime | None` to `state.py` (TDD it with one test if you prefer), and call that — do **not** reach into a private name. The `# noqa` above is a placeholder marking the spot to fix; resolve it, don't ship it. Equally, `--command nargs=REMAINDER` with no following args yields `[]`, which the validator rejects (empty command) — that drives `test_add_rejects_empty_command`.

- [ ] **Step 4: Add the public `parse_ts_or_none` helper to `state.py`**

```python
# src/cc_session_tools/lib/scheduler/state.py  (add)
def parse_ts_or_none(value: str | None) -> datetime | None:
    if value is None:
        return None
    from datetime import timezone
    return datetime.strptime(value, _TS_FMT).replace(tzinfo=timezone.utc)
```

Then change `_cmd_list` to use `state.parse_ts_or_none(...)` (falling back to `now` only when both `last_success` and the state entry are absent).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_ccsched_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/cli/ccsched.py src/cc_session_tools/lib/scheduler/state.py tests/scheduler/test_ccsched_cli.py
git commit -m "feat(ccsched): CLI (add/list/edit/enable/disable/remove/run/status/sweep)

[Cld]"
```

---

# Phase E — hook wiring

### Task 12: `catchup` hook + `HOOK_VERBS` + bundle entry + packaging

**Files:**
- Create: `src/cccs_hooks/catchup.py`
- Modify: `src/cc_session_tools/cli/ccst.py` (add `catchup` to `HOOK_VERBS` + `HOOK_DESCRIPTIONS`)
- Modify: `config/hooks-bundle.json` (add `catchup` to `SessionStart`)
- Modify: `pyproject.toml` (add `ccsched` script; bump 0.13.0 → 0.14.0)
- Test: `tests/scheduler/test_catchup_hook.py`

**Responsibilities:** the hook reads stdin JSON (session context is unused by the sweep but parsed for symmetry / future use), calls `sweep.run_sweep(now=datetime.now(...))`, formats the digest, and emits `additionalContext`. It must **never raise** (§15): on any failure it emits empty `additionalContext` and logs via `ledger`/`telemetry`. The bundle entry mirrors `messaging-deliver` (`timeout: 10`, a `statusMessage`).

- [ ] **Step 1: Write the failing test**

```python
# tests/scheduler/test_catchup_hook.py
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cccs_hooks import catchup
from cc_session_tools.lib.scheduler import registry, state
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields


@pytest.fixture(autouse=True)
def _dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(catchup, "_emit", lambda ctx, event: out.append(ctx))
    return out


def test_hook_emits_digest_for_overdue_job(monkeypatch: pytest.MonkeyPatch) -> None:
    registry.add_job(validate_job_fields(
        job_id="tesco", cadence="daily@09:00", coalesce="one", command=["true"],
        surface=True, enabled=True, catchup_window="30d", timeout="5s",
    ))
    state.save_all_state({"tesco": state.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0)})
    # Freeze now so the daily job is overdue.
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    _stdin(monkeypatch, {"hookEventName": "SessionStart", "session_id": "u", "cwd": "/tmp"})
    out = _capture(monkeypatch)
    assert catchup.main() == 0
    assert any("tesco" in e for e in out)


def test_hook_emits_empty_on_bad_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    out = _capture(monkeypatch)
    assert catchup.main() == 0
    assert out == [""]


def test_failure_path_writes_to_env_ledger_not_real_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The _dirs autouse fixture points CCCS_HOOKS_DIR at tmp_path/hooks. The bad-stdin
    # failure path must log there, NOT to the real ~/.claude/hooks/fires.jsonl. If
    # _log_failure ever drops the hooks_dir= argument, log_event falls back to
    # Path.home()/.claude/hooks and this test fails. Guard the real home with a sentinel.
    real_fires = Path.home() / ".claude" / "hooks" / "fires.jsonl"
    before = real_fires.read_text() if real_fires.is_file() else None
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    _capture(monkeypatch)
    assert catchup.main() == 0
    env_fires = tmp_path / "hooks" / "fires.jsonl"
    assert env_fires.is_file()
    assert "sweep-failed:bad-stdin" in env_fires.read_text()
    after = real_fires.read_text() if real_fires.is_file() else None
    assert after == before  # real ledger untouched


def test_hook_never_raises_on_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    state.scheduler_dir().mkdir(parents=True, exist_ok=True)
    registry.registry_path().write_text("[[job]\nbroken")
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    _stdin(monkeypatch, {"hookEventName": "SessionStart", "session_id": "u", "cwd": "/tmp"})
    out = _capture(monkeypatch)
    assert catchup.main() == 0
    assert any("failed to parse" in e for e in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_catchup_hook.py -q`
Expected: FAIL (`ModuleNotFoundError: cccs_hooks.catchup`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/cccs_hooks/catchup.py
"""SessionStart hook: reconcile scheduled jobs and inject a catch-up digest.

Runs the scheduler sweep and emits a compact additionalContext digest of what
ran / was missed / failed. Never blocks a session: any failure degrades to an
empty additionalContext and is logged to telemetry (§15)."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from cc_session_tools.lib.scheduler import ledger, sweep
from cc_session_tools.lib.scheduler.digest import format_digest
from cccs_hooks.telemetry import TelemetryEntry, log_event

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(context: str, event: str) -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}},
        sys.stdout,
    )


def _log_failure(reason: str) -> None:
    # Route through ledger._hooks_dir() so CCCS_HOOKS_DIR is honoured. telemetry.log_event
    # does NOT read CCCS_HOOKS_DIR itself (only telemetry.main() does), so without an
    # explicit hooks_dir= this would write to the real ~/.claude/hooks/fires.jsonl even
    # under tests that set CCCS_HOOKS_DIR — polluting the user's real ledger (§15).
    log_event(
        TelemetryEntry(
            hook="catchup", event="", tool="", session_id="", cwd_short="",
            decision="annotate", cache="none", verdict=f"sweep-failed:{reason}",
            input_hash="",
        ),
        hooks_dir=ledger._hooks_dir(),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _log_failure("bad-stdin")
        _emit("", "SessionStart")
        return 0
    event = str(data.get("hookEventName", "SessionStart"))
    try:
        result = sweep.run_sweep(now=_now())
        digest = format_digest(result.reports, parse_error=result.parse_error)
    except (OSError, ValueError) as exc:
        _log_failure(type(exc).__name__)
        _emit("", event)
        return 0
    _emit(digest, event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```python
# src/cc_session_tools/cli/ccst.py — add to HOOK_VERBS (after messaging-deliver)
    "catchup": "cccs_hooks.catchup",
```

```python
# src/cc_session_tools/cli/ccst.py — add to HOOK_DESCRIPTIONS
    "catchup": "Reconciles missed scheduled jobs (ccsched) on session start and injects a catch-up digest",
```

```json
// config/hooks-bundle.json — append to SessionStart.hooks[]
{
  "type": "command",
  "command": "ccst hooks run catchup",
  "timeout": 10,
  "statusMessage": "Reconciling scheduled-task catch-up..."
}
```

```toml
# pyproject.toml — [project.scripts] add
ccsched = "cc_session_tools.cli.ccsched:main"
```

```toml
# pyproject.toml — bump version
version = "0.14.0"
```

- [ ] **Step 4: Run the hook test**

Run: `uv run pytest tests/scheduler/test_catchup_hook.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the bundle is valid JSON and the hook dispatches**

Run: `uv run python -c "import json,pathlib; json.loads(pathlib.Path('config/hooks-bundle.json').read_text()); print('ok')"`
Expected: `ok`

Run: `echo '{"hookEventName":"SessionStart","session_id":"x","cwd":"/tmp"}' | CC_SCHEDULER_DIR=/tmp/ccsched-smoke uv run python -m cc_session_tools.cli.ccst hooks run catchup; echo; rm -rf /tmp/ccsched-smoke`
Expected: a JSON object with `hookSpecificOutput.additionalContext` (empty string when no jobs).

- [ ] **Step 6: Commit**

```bash
git add src/cccs_hooks/catchup.py src/cc_session_tools/cli/ccst.py config/hooks-bundle.json pyproject.toml tests/scheduler/test_catchup_hook.py
git commit -m "feat(hooks): catchup SessionStart hook + ccsched script + version 0.14.0

[Cld]"
```

---

# Phase F — skill + docs + verification

### Task 13: `manage-recurring-cc-jobs-using-ccsched` skill (doc-only)

**Files:**
- Create: `skills/manage-recurring-cc-jobs-using-ccsched/SKILL.md`

**Responsibilities:** a doc-only skill (auto-discovered by `ccst skills install` because it has a `SKILL.md`; no testpaths change). It guides Claude to translate a natural-language request into a validated `ccsched add`, reminds of the idempotency contract (§10), disambiguates the three schedulers (§12), and warns about the migration double-fire window (§18).

- [ ] **Step 1: Create the skill file**

```markdown
---
name: manage-recurring-cc-jobs-using-ccsched
description: Use when the user wants a local command to run on a recurring cadence on this laptop and to be caught up after the machine has been off - "run X every day", "check Tesco every morning", "weekly calendar sync", "schedule a local job", "add a recurring job", "/manage-recurring-cc-jobs-using-ccsched". Translates the request into a validated `ccsched add`. Do NOT use for cloud cron agents (that is `/schedule`) or for polling within one live session (that is `/loop`).
---

# Manage recurring CC jobs with ccsched

`ccsched` registers a local job that is **reconciled on Claude Code session
start**: if the laptop was off when a run was due, the next session backfills it.
There is no live timer to miss.

## First: which scheduler?

Disambiguate before doing anything:

1. **`ccsched`** (this skill) - a local command (argv) that should run on a
   cadence and be **caught up** after the laptop was off. Trigger: a periodic
   local task on this machine (Tesco check, calendar sync).
2. **`/schedule`** - a **cloud cron** agent that runs on Anthropic's
   infrastructure regardless of whether the laptop is on. Use when the run must
   happen at a wall-clock instant even with the laptop off.
3. **`/loop`** - poll/repeat **within one live session**. Use for "keep checking
   every 5 minutes while I work".

If the user wants away-from-laptop delivery (e.g. a phone push), `ccsched` cannot
do it - that needs an always-on host. Say so.

## Translate the request into `ccsched add`

Map the natural-language cadence to the grammar:

| User says | `--cadence` |
|-----------|-------------|
| every 6 hours | `every:6h` |
| every morning at 9 | `daily@09:00` |
| Mondays at 7:30 | `weekly:mon@07:30` |
| the 1st of each month | `monthly:1@09:00` |

Then:

```sh
ccsched add --id <kebab-id> --cadence <cadence> \
  --coalesce one \
  --catchup-window 7d --timeout 60s \
  --command <argv...>
```

- `--id` must be unique kebab-case.
- `--command` takes the **whole argv** after it (e.g. `--command ccst hooks run check-tesco-due`).
- `--coalesce one` (default) collapses N missed runs into one - right for
  "current state" jobs. Use `--coalesce each` only when every missed period must
  produce its own artefact.

## The idempotency contract

A registered job **must be safe to run late and safe to coalesce**. The scheduler
reduces re-runs but cannot make a non-idempotent command safe. Before adding a
job, confirm the command does the right thing when run once, late, after several
missed days.

## Migrating an existing always-fire SessionStart hook

If a job currently fires as a plain SessionStart hook (e.g. the Tesco or
calendar checks) and you add a `ccsched` entry for it, it will run **twice** until
the old hook is removed. Migrate in this order: (1) add the registry entry,
(2) then remove the old SessionStart hook. Never the reverse.

## Inspecting

- `ccsched list` - cadence, enabled, last_success, next_due.
- `ccsched status [<id>]` - recent ledger outcomes from fires.jsonl.
- `ccsched run <id> --force` - run now (for testing the command).
- `ccsched sweep` - run the reconcile sweep manually.
```

- [ ] **Step 2: Verify the skill is discoverable (dry run)**

Run: `uv run python -m cc_session_tools.cli.ccst skills install --source skills --target /tmp/ccsched-skills-test 2>&1 | grep manage-recurring-cc-jobs-using-ccsched && rm -rf /tmp/ccsched-skills-test`
Expected: the `manage-recurring-cc-jobs-using-ccsched` row appears with action `create`.

- [ ] **Step 3: Commit**

```bash
git add skills/manage-recurring-cc-jobs-using-ccsched/SKILL.md
git commit -m "feat(skills): manage-recurring-cc-jobs-using-ccsched

[Cld]"
```

---

### Task 14: README, CHANGELOG, installer verify, full-suite gate

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml` (already bumped in Task 12; confirm `testpaths` collects `tests/scheduler` via the existing `tests` root)
- Verify: `install-everything.sh` (no edit expected)

This task is docs/config; it ends with a full `mypy --strict` + `uv run pytest` run as the verification gate.

- [ ] **Step 1: Add the README section + table entries**

Add a new top-level section `## Scheduled-task catch-up` after `## Inter-session messaging` describing: the reconcile-on-session-start model, the registry (`~/.claude/cc-scheduler/jobs.toml`), the `ccsched` subcommand table (add / list / edit / enable / disable / remove / run / status / sweep), the cadence grammar, coalescing, the digest, and the three-scheduler disambiguation. Add:
- a "Bundled skills" entry for `manage-recurring-cc-jobs-using-ccsched`,
- a "Hook library" note for `catchup` (SessionStart),
- a session-management CLIs entry for `ccsched`.

Keep prose to the repo's existing style; do not restate the spec.

- [ ] **Step 2: Add the CHANGELOG entries**

Add a new block above `## [0.13.0]`:

```markdown
## [0.14.0] - 2026-06-22

### Added

- **Scheduled-task catch-up.** A new `ccsched` CLI registers local recurring
  jobs in `~/.claude/cc-scheduler/jobs.toml`. Jobs run on a declared cadence
  (`every:`/`daily@`/`weekly:`/`monthly:`) and are reconciled on Claude Code
  session start: runs missed while the laptop was off are back-filled, coalesced
  per the job's `coalesce` setting (`one`/`each`). Subcommands: `add`, `list`,
  `edit`, `enable`, `disable`, `remove`, `run`, `status`, `sweep`.
- **`catchup` SessionStart hook** runs the reconcile sweep (time-boxed,
  sequential, per-sweep cap, sweep-locked) and injects a compact catch-up digest
  as additional context. Failures never block the session; every action is
  recorded to the shared `fires.jsonl` telemetry ledger.
- **`manage-recurring-cc-jobs-using-ccsched` skill** translates natural-language
  cadence requests into validated `ccsched add` calls and disambiguates `ccsched`
  vs `/schedule` (cloud cron) vs `/loop` (in-session poll).
```

- [ ] **Step 3: Verify the installer needs no new step**

The registry is lazy-created; steps 1–3 of `install-everything.sh` already
install the new CLI (`ccsched` script), skill, and hook on `--upgrade`. Confirm
the installer still parses:

Run: `bash -n install-everything.sh && echo ok`
Expected: `ok`

- [ ] **Step 4: Confirm test collection includes the scheduler suite**

Run: `uv run pytest tests/scheduler -q --collect-only | tail -3`
Expected: a non-zero count of collected tests under `tests/scheduler/`.

- [ ] **Step 5: Run the full verification gate**

Run: `uv sync --extra dev && uv run mypy --strict src/cc_session_tools/lib/scheduler src/cc_session_tools/cli/ccsched.py src/cccs_hooks/catchup.py`
Expected: `Success: no issues found`.

Run: `uv run pytest -q`
Expected: all tests pass (existing + new). Investigate and fix any failure before proceeding — do not disable checks. If pre-existing failures exist on the branch, fix them first (you own the codebase while you are in it).

- [ ] **Step 6: Commit**

```bash
git add README.md CHANGELOG.md pyproject.toml
git commit -m "docs: scheduled-task catch-up README + CHANGELOG (0.14.0)

[Cld]"
```

---

## Final review

After Task 14, run @superpowers:requesting-code-review (or dispatch the `superpowers:code-reviewer` agent) against the full diff with the spec and coding standards in hand, then run `ccst doctor` against a tmp settings.json to confirm the new `catchup` hook registers cleanly. Then use @superpowers:finishing-a-development-branch to decide on merge/PR. (Do **not** push or open a PR without the user's explicit instruction; PR bodies end with a line containing only `[Cld]`.)

## Notes for the implementer (gotchas)

- **`uv run` everywhere; never `uv tool install` from a worktree** (project CLAUDE.md) — it overwrites the global install's source pointer and breaks the CLIs when the worktree is deleted. After the PR merges, reinstall the global tool from `main` per the project CLAUDE.md.
- **Never touch real `~/.claude/`** in tests — every test sets `CC_SCHEDULER_DIR` and `CCCS_HOOKS_DIR` to tmp paths. The autouse conftest fixture clears the roots env vars already; the scheduler does not use those, but keep all state under tmp.
- **Pure vs I/O separation is load-bearing for testability.** `due.py`/`cadence.py`/`duration.py`/`digest.py`/`jobspec.py` must have **zero** filesystem or `datetime.now()` calls — `now` is always injected. If a test for these needs `tmp_path`, the boundary has leaked; fix it.
- **The single sanctioned error-to-empty conversion is the `catchup` hook.** Everywhere else, let specific exceptions propagate. `RegistryError` for a malformed `jobs.toml` is surfaced as a digest *warning* (not swallowed) via `SweepResult.parse_error`.
- **Resolve every placeholder before shipping a green test:** the `failed` boolean in `sweep._run_one` (simplify to `timed_out or exit_code != 0`), the `# noqa`/private-name access in `ccsched._cmd_list` (use the public `state.parse_ts_or_none`). The repo's coding standards forbid dead clauses and private-name reach-ins.
- **`mypy --strict` will want explicit types** on the injected `runner`/`clock` callables in `sweep.run_sweep` (the `Runner`/`Clock` aliases are provided) and on the `tomllib` table access in `registry._spec_from_table` (the `command` field comes back as `list[object]`; coerce with `[str(x) for x in ...]`).
- **DST contract (§17.3):** wall-clock cadences are computed naively on the local calendar — one instant per calendar occurrence — so a job fires once per period across DST transitions and never twice. Document this in `due.py` (done in the module docstring) rather than special-casing DST.
