# ccsched auto-suspend on repeated failure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A scheduled job that fails every time it runs (misconfigured command, missing binary, etc.) auto-suspends after 10 consecutive failures instead of storm-retrying on every `SessionStart`/throttled `UserPromptSubmit` forever, and a Telegram push fires the moment it suspends so a permanently-broken job in a rarely-opened project doesn't go unnoticed.

**Architecture:** Extend `state.json`'s existing per-job record with one new field, `suspended: bool`. `reconcile_and_launch()` (the auto-trigger path invoked by the `catchup` hook on every `SessionStart`/throttled `UserPromptSubmit`) skips any job with `suspended=True`, exactly the way it already skips `enabled=False` jobs. The detached worker (`worker.py`, where failures are already counted) flips `suspended=True` the moment `consecutive_failures` crosses a threshold (10) and fires one push notification via a new `notify.py` module that talks to the Telegram Bot API directly — no MCP tool, no live Claude session required, since this runs from a headless subprocess. `ccsched enable <job>` (already the documented recovery command) clears the suspension alongside its existing jobs.toml `enabled=true` flip. The digest gets one new, always-surfaced line for the suspend event, backed by a new `LedgerEvent.SUSPEND`.

**Tech Stack:** Python 3.11+, stdlib only (`urllib.request` for the Telegram call — no new dependency), existing dataclass/state-machine patterns in `src/cc_session_tools/lib/scheduler/`, pytest with `tmp_path`/`monkeypatch` env-var redirection (`CC_SCHEDULER_DIR`, `CCCS_HOOKS_DIR`).

**Repo/worktree:** This plan is executed in the worktree at
`~/repos/claude-code-session-tools/.worktrees/ccsched-backoff` on branch
`f/20260710-ccsched-backoff` (created off `main`, since `main` itself is not
checked out in the primary working directory right now — it has unrelated
in-progress work on branch `f/20260709-work`; do not touch that branch or its
files). Run `uv sync --extra dev` there before starting if not already done.
All commands below assume `cwd` is that worktree. Use `uv run` for every test/mypy
invocation — never `uv tool install` from the worktree.

**Out of scope (confirmed during investigation, do not touch):**
- The `ccmsg-dead-letter-sweep` job itself — already fixed, `consecutive_failures: 0`.
- `_cmd_run` (manual `ccsched run <id>`) gaining its own suspend+notify trigger. It's
  a synchronous, user-invoked command — a human is already watching, so a push
  notification would be redundant. It DOES need one small fix (Task 7) so it stops
  silently clearing an existing suspension, but it will not itself cause one.
- Per-job configurable thresholds (jobs.toml schema change). YAGNI — not requested.
- Unifying the `_run_body` / `_cmd_run` failure-state-construction duplication beyond
  what Task 7 requires. That's pre-existing tech debt, not this ticket's job.

---

### Task 1: `JobState.suspended` field + pure threshold decision + `clear_suspended()`

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/state.py`
- Test: `tests/scheduler/test_state.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/scheduler/test_state.py`:

```python
def test_round_trip_preserves_suspended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    states = {
        "j": st.JobState(
            registered_at="2026-06-20T00:00:00Z", last_success=None,
            last_attempt=None, consecutive_failures=10, in_flight=None,
            suspended=True,
        )
    }
    st.save_all_state(states)
    assert st.load_all_state() == states


def test_default_suspended_is_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    js = st.ensure_registered({}, "new-job", now)
    assert js.suspended is False


def test_next_failure_count_increments_below_threshold() -> None:
    new_count, suspended, newly = st.next_failure_count(3, suspended=False, threshold=10)
    assert (new_count, suspended, newly) == (4, False, False)


def test_next_failure_count_suspends_at_threshold() -> None:
    new_count, suspended, newly = st.next_failure_count(9, suspended=False, threshold=10)
    assert (new_count, suspended, newly) == (10, True, True)


def test_next_failure_count_past_threshold_does_not_resuspend() -> None:
    # Already suspended (e.g. a job that keeps getting manually re-run and re-failing) —
    # newly_suspended must be False so callers don't re-notify every time.
    new_count, suspended, newly = st.next_failure_count(15, suspended=True, threshold=10)
    assert (new_count, suspended, newly) == (16, True, False)


def test_clear_suspended_resets_flag_and_leaves_rest_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=12, in_flight=None, suspended=True)})
    st.clear_suspended("j")
    after = st.load_all_state()["j"]
    assert after.suspended is False
    assert after.consecutive_failures == 12  # untouched — only the flag clears


def test_clear_suspended_on_unknown_job_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.clear_suspended("ghost")  # must not raise
    assert st.load_all_state() == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_state.py -q`
Expected: FAIL — `JobState` has no `suspended` field, `next_failure_count`/`clear_suspended` don't exist.

- [ ] **Step 3: Implement**

In `state.py`:

1. Add module constant near the top (after `_UTC = timezone.utc`):

```python
DEFAULT_SUSPEND_THRESHOLD = 10
```

2. Add the field to `JobState` (keep it last so every existing keyword-arg call site
   in the codebase — verified via `grep -rn "JobState(" src/ tests/`, all use kwargs —
   keeps working unmodified):

```python
@dataclass(frozen=True, slots=True)
class JobState:
    registered_at: str
    last_success: str | None
    last_attempt: str | None
    consecutive_failures: int
    in_flight: InFlight | None = None
    suspended: bool = False
```

3. `load_all_state`: read it back —

```python
consecutive_failures=int(fields.get("consecutive_failures", 0)),
in_flight=_in_flight_from(fields.get("in_flight")),
suspended=bool(fields.get("suspended", False)),
```

4. `save_all_state`: serialise it —

```python
"consecutive_failures": js.consecutive_failures,
"suspended": js.suspended,
"in_flight": (...),
```

5. `ensure_registered`: the fresh-job branch already lists every field explicitly —
   add `suspended=False,`.

6. `_replace(...)` (used only by `set_in_flight`/`clear_in_flight` today) must keep
   passing through `suspended=js.suspended` — it already forwards every other field,
   so add `suspended=js.suspended` to its `JobState(...)` construction.

7. Add the pure decision function (place it near `_replace`, before `set_in_flight`):

```python
def next_failure_count(
    consecutive_failures: int, *, suspended: bool, threshold: int = DEFAULT_SUSPEND_THRESHOLD
) -> tuple[int, bool, bool]:
    """Pure: given the current consecutive_failures/suspended, return
    (new_consecutive_failures, new_suspended, newly_suspended). ``newly_suspended``
    is True only the instant the threshold is first crossed, so callers notify
    exactly once per suspend event rather than on every failure after."""
    new_consecutive = consecutive_failures + 1
    newly_suspended = not suspended and new_consecutive >= threshold
    return new_consecutive, suspended or newly_suspended, newly_suspended
```

8. Add the atomic clear, mirroring `clear_in_flight`:

```python
def clear_suspended(job_id: str) -> None:
    """Atomic read-modify-write clearing one job's suspended flag (mirrors
    clear_in_flight). A no-op if the job has no state yet."""
    states = load_all_state()
    if job_id in states:
        js = states[job_id]
        states[job_id] = JobState(
            registered_at=js.registered_at, last_success=js.last_success,
            last_attempt=js.last_attempt, consecutive_failures=js.consecutive_failures,
            in_flight=js.in_flight, suspended=False,
        )
        save_all_state(states)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_state.py -q`
Expected: PASS, all tests including the pre-existing ones in this file.

- [ ] **Step 5: mypy**

Run: `uv run mypy --strict src/cc_session_tools/lib/scheduler/state.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/state.py tests/scheduler/test_state.py
git commit -m "feat(scheduler): add suspended flag + failure-threshold helper to JobState"
```

---

### Task 2: `notify.py` — Telegram push, no live session required

**Why a new module:** the existing `notify-user` skill (`~/.claude/skills/notify-user/SKILL.md`)
only exists for a *live Claude Code session* to invoke via Bash — it's not usable from
`worker.py`, which runs as a detached subprocess with no LLM in the loop. The skill's
own mechanism is simple enough to reproduce directly in Python: POST to the Telegram
Bot API using `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. Those are not guaranteed to be
in a detached subprocess's inherited environment (they're normally sourced from
`~/.creds` by an interactive shell profile), so this module reads the environment
first and falls back to parsing `~/.creds` directly.

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/notify.py`
- Test: `tests/scheduler/test_notify.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/scheduler/test_notify.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import notify


def _spy_post() -> tuple[list[tuple[str, bytes]], notify.Poster]:
    calls: list[tuple[str, bytes]] = []

    def post(url: str, data: bytes) -> None:
        calls.append((url, data))

    return calls, post


def test_send_uses_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat456")
    calls, post = _spy_post()
    assert notify.send_telegram("hello", post=post) is True
    assert len(calls) == 1
    url, data = calls[0]
    assert "tok123" in url
    assert b"hello" in data
    assert b"chat456" in data


def test_send_falls_back_to_creds_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    creds = tmp_path / "creds"
    creds.write_text('export TELEGRAM_BOT_TOKEN="filetok"\nTELEGRAM_CHAT_ID=filechat\n')
    monkeypatch.setenv("CCCS_CREDS_PATH", str(creds))
    calls, post = _spy_post()
    assert notify.send_telegram("hello", post=post) is True
    assert "filetok" in calls[0][0]
    assert b"filechat" in calls[0][1]


def test_send_returns_false_when_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("CCCS_CREDS_PATH", str(tmp_path / "nope"))
    calls, post = _spy_post()
    assert notify.send_telegram("hello", post=post) is False
    assert calls == []


def test_send_returns_false_on_post_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    def failing_post(url: str, data: bytes) -> None:
        raise OSError("network down")

    assert notify.send_telegram("hello", post=failing_post) is False


def test_suspended_message_names_job_and_enable_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    calls, post = _spy_post()
    notify.suspended("ccmsg-dead-letter-sweep", 10, post=post)
    _, data = calls[0]
    assert b"ccmsg-dead-letter-sweep" in data
    assert b"10 consecutive" in data
    assert b"ccsched enable ccmsg-dead-letter-sweep" in data
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_notify.py -q`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

Create `src/cc_session_tools/lib/scheduler/notify.py`:

```python
"""Best-effort Telegram push for events a headless scheduler worker needs to
surface even when no Claude Code session is open to read the digest. Talks to
the Telegram Bot API directly over HTTPS — the same mechanism the interactive
`notify-user` skill uses, reproduced here because a detached `ccsched _run-job`
subprocess has no LLM in the loop to invoke a skill through.

Credentials come from the environment (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
if already exported, else are parsed directly from ``~/.creds`` (override via
``CCCS_CREDS_PATH``) since a detached subprocess's inherited environment is not
guaranteed to have sourced a shell profile. Every failure mode degrades to a
logged warning and a ``False`` return — a notification that can't be sent must
never take down the worker it's reporting on."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_CREDS_PATH_ENV = "CCCS_CREDS_PATH"
_API_BASE = "https://api.telegram.org"

Poster = Callable[[str, bytes], None]


def _creds_path() -> Path:
    raw = os.environ.get(_CREDS_PATH_ENV)
    return Path(raw).expanduser() if raw else Path.home() / ".creds"


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ").strip()
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _credentials() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        parsed = _parse_env_file(_creds_path())
        token = token or parsed.get("TELEGRAM_BOT_TOKEN")
        chat_id = chat_id or parsed.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    return token, chat_id


def _default_post(url: str, data: bytes) -> None:
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    urllib.request.urlopen(req, timeout=10)  # noqa: S310 -- fixed https host, not user input


def send_telegram(message: str, *, post: Poster = _default_post) -> bool:
    """Best-effort send. Returns False (and logs) on missing credentials or any
    transport failure — never raises, so a broken notification path can't crash
    the scheduler worker it's meant to be reporting a failure from."""
    creds = _credentials()
    if creds is None:
        logger.warning(
            "telegram notify skipped: no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
            "in env or %s", _creds_path(),
        )
        return False
    token, chat_id = creds
    url = f"{_API_BASE}/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
    try:
        post(url, payload)
    except (OSError, ValueError) as exc:
        logger.warning("telegram notify failed: %s", exc)
        return False
    return True


def suspended(job_id: str, consecutive_failures: int, *, post: Poster = _default_post) -> bool:
    """The one-time push fired when a job crosses the auto-suspend threshold."""
    message = (
        f"[cc-scheduler] {job_id} auto-suspended after {consecutive_failures} "
        f"consecutive failures — see fires.jsonl / run "
        f"`ccsched enable {job_id}` after fixing"
    )
    return send_telegram(message, post=post)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_notify.py -q`
Expected: PASS

- [ ] **Step 5: mypy**

Run: `uv run mypy --strict src/cc_session_tools/lib/scheduler/notify.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/notify.py tests/scheduler/test_notify.py
git commit -m "feat(scheduler): add best-effort Telegram push for headless workers"
```

---

### Task 3: `LedgerEvent.SUSPEND`

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/ledger.py`
- Test: `tests/scheduler/test_ledger.py`

- [ ] **Step 1: Write the failing test**

Read `tests/scheduler/test_ledger.py` first to match its exact style (likely asserts
on `record()` + `read_recent()` round-tripping enum values). Add:

```python
def test_suspend_event_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    ld.record(ld.LedgerEntry(
        job_id="broken-job", event=ld.LedgerEvent.SUSPEND, owed=0, ran=0,
        exit_code=None, duration_ms=0, error=None, consecutive_failures=10,
    ))
    row = ld.read_recent(job_id="broken-job")[-1]
    assert row["event"] == "suspend"
    assert row["consecutive_failures"] == 10
```

(Match fixture/import names to whatever `test_ledger.py` already uses — read it
before writing this so it's consistent, not a guess.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_ledger.py -q`
Expected: FAIL — `LedgerEvent.SUSPEND` doesn't exist.

- [ ] **Step 3: Implement**

In `ledger.py`, add one line to the enum:

```python
class LedgerEvent(str, Enum):
    LAUNCH = "launch"
    RUN = "run"
    BACKFILL = "backfill"
    SKIP_EXPIRED = "skip_expired"
    DEFER = "defer"
    FAIL = "fail"
    SUSPEND = "suspend"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_ledger.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/ledger.py tests/scheduler/test_ledger.py
git commit -m "feat(scheduler): add SUSPEND ledger event"
```

---

### Task 4: Wire suspend detection + notification into `worker.py`

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/worker.py`
- Test: `tests/scheduler/test_worker.py`

This is the core of the fix: the FAIL branch of `_run_body` already increments
`consecutive_failures` — it now also decides (via `state.next_failure_count`)
whether this failure crosses the suspend threshold, persists `suspended` on the
`JobState`, and — only on the instant it first crosses — records a `SUSPEND`
ledger entry and fires the Telegram push.

- [ ] **Step 1: Write the failing tests**

Add to `tests/scheduler/test_worker.py`:

```python
def test_tenth_consecutive_failure_suspends_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("broken")
    st.save_all_state({"broken": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=9, in_flight=None, suspended=False)})
    notified: list[tuple[str, int]] = []

    def fake_notify(job_id: str, consecutive_failures: int) -> bool:
        notified.append((job_id, consecutive_failures))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("broken", instants=1, now=now, runner=_fail_runner, notify_suspended=fake_notify)

    after = st.load_all_state()["broken"]
    assert after.consecutive_failures == 10
    assert after.suspended is True
    assert notified == [("broken", 10)]
    rows = ld.read_recent(job_id="broken")
    assert rows[-1]["event"] == ld.LedgerEvent.SUSPEND.value


def test_eleventh_consecutive_failure_does_not_renotify(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("broken")
    st.save_all_state({"broken": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    notified: list[tuple[str, int]] = []

    def fake_notify(job_id: str, consecutive_failures: int) -> bool:
        notified.append((job_id, consecutive_failures))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("broken", instants=1, now=now, runner=_fail_runner, notify_suspended=fake_notify)

    assert notified == []  # already suspended — no repeat push
    rows = ld.read_recent(job_id="broken")
    assert rows[-1]["event"] == ld.LedgerEvent.FAIL.value  # still a FAIL, no new SUSPEND


def test_healthy_job_never_suspends(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _seed("tesco")
    notified: list[tuple[str, int]] = []
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("tesco", instants=1, now=now, runner=_ok_runner,
               notify_suspended=lambda j, n: notified.append((j, n)) or True)
    after = st.load_all_state()["tesco"]
    assert after.suspended is False
    assert notified == []


def test_success_preserves_existing_suspended_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # A manually re-run suspended job that happens to succeed once should NOT
    # silently clear its own suspension — only `ccsched enable` does that
    # (Task 6). Otherwise a flaky-but-still-broken job could un-suspend itself
    # on a lucky run and go straight back to storm-retrying.
    _add("flaky")
    st.save_all_state({"flaky": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("flaky", instants=1, now=now, runner=_ok_runner)
    after = st.load_all_state()["flaky"]
    assert after.consecutive_failures == 0  # success still resets the streak
    assert after.suspended is True  # but does not clear suspension
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_worker.py -q`
Expected: FAIL — `run_job`/`_run_body` don't accept `notify_suspended`, `suspended`
isn't tracked, `SUSPEND` events aren't written.

- [ ] **Step 3: Implement**

In `worker.py`:

1. Import the new pieces:

```python
from cc_session_tools.lib.scheduler import ledger, notify, registry, state
```

2. Add the injectable notifier type near `Runner`:

```python
NotifySuspended = Callable[[str, int], bool]
```

3. Thread `notify_suspended` through `run_job` → `_run_body`:

```python
def _run_body(
    spec: JobSpec, instants: int, now: datetime, runner: Runner,
    notify_suspended: NotifySuspended,
) -> None:
    ...
```

```python
def run_job(
    job_id: str, *, instants: int, now: datetime, runner: Runner = run_command,
    notify_suspended: NotifySuspended = notify.suspended,
) -> None:
    spec = _load_spec(job_id)
    try:
        with in_flight_lock(job_id):
            try:
                ...
                _run_body(spec, instants, now, runner, notify_suspended)
            finally:
                state.clear_in_flight(job_id)
    except InFlightLockHeld:
        ...
```

4. Replace the failure branch's state construction:

```python
    if failed:
        new_consecutive, new_suspended, newly_suspended = state.next_failure_count(
            cur.consecutive_failures, suspended=cur.suspended,
        )
        states[spec.job_id] = JobState(
            registered_at=cur.registered_at, last_success=cur.last_success,
            last_attempt=attempt_ts, consecutive_failures=new_consecutive,
            in_flight=cur.in_flight, suspended=new_suspended,
        )
        state.save_all_state(states)
        _record(spec, LedgerEvent.FAIL, owed_n, 0, last_outcome,
                (last_outcome.stderr.strip()[:200] if last_outcome else None)
                or ("timed out" if last_outcome and last_outcome.timed_out else None),
                consecutive_failures=new_consecutive)
        if newly_suspended:
            notify_suspended(spec.job_id, new_consecutive)
            _record(spec, LedgerEvent.SUSPEND, owed_n, 0, None, None,
                    consecutive_failures=new_consecutive)
        return
```

5. The success branch already reconstructs `JobState` explicitly — add
   `suspended=cur.suspended` so a success never silently un-suspends:

```python
    states[spec.job_id] = JobState(
        registered_at=cur.registered_at, last_success=new_success,
        last_attempt=attempt_ts, consecutive_failures=0, in_flight=cur.in_flight,
        suspended=cur.suspended,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_worker.py -q`
Expected: PASS, all tests including pre-existing ones.

- [ ] **Step 5: mypy**

Run: `uv run mypy --strict src/cc_session_tools/lib/scheduler/worker.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/worker.py tests/scheduler/test_worker.py
git commit -m "feat(scheduler): auto-suspend a job after 10 consecutive failures, notify once"
```

---

### Task 5: `reconcile_and_launch` skips suspended jobs

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/reconcile.py`
- Test: `tests/scheduler/test_reconcile.py`

This is the other half of the actual incident fix: even once a job is marked
`suspended`, nothing stops the hook-triggered reconcile sweep from launching it
again unless it explicitly checks the flag.

- [ ] **Step 1: Write the failing test**

Add to `tests/scheduler/test_reconcile.py`:

```python
def test_suspended_job_not_launched(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("broken")
    st.save_all_state({"broken": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=10, in_flight=None, suspended=True)})
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = rc.reconcile_and_launch(now=now, spawn=spawn)
    assert "broken" not in result.launched
    assert spawn.calls == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_reconcile.py -q`
Expected: FAIL — job still launches (the whole point of the original bug).

- [ ] **Step 3: Implement**

In `reconcile.py`, inside the `for spec in specs:` loop, right after
`js = state.ensure_registered(states, spec.job_id, now)` and before the
`in_flight`/`pid_alive` check:

```python
        js = state.ensure_registered(states, spec.job_id, now)
        if js.suspended:
            continue  # auto-suspended after repeated failures; ccsched enable to resume
        if js.in_flight is not None and pid_alive(js.in_flight.pid):
            continue  # fast-path skip; not the correctness guarantee (§9.1)
```

No new ledger event here — the suspend event was already recorded once, at the
moment `worker.py` set the flag (Task 4). Re-logging a skip on every subsequent
sweep would just reproduce the original noise problem in a new form.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_reconcile.py -q`
Expected: PASS, all tests including pre-existing ones.

- [ ] **Step 5: mypy**

Run: `uv run mypy --strict src/cc_session_tools/lib/scheduler/reconcile.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/reconcile.py tests/scheduler/test_reconcile.py
git commit -m "fix(scheduler): reconcile_and_launch skips auto-suspended jobs"
```

---

### Task 6: Digest line for the suspend event

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/digest.py`
- Modify: `src/cc_session_tools/lib/scheduler/surface.py`
- Test: `tests/scheduler/test_digest.py`
- Test: `tests/scheduler/test_surface.py`

**Files:**

- [ ] **Step 1: Write the failing tests**

Add to `tests/scheduler/test_digest.py`:

```python
def test_suspended_job_always_surfaces_even_when_silent() -> None:
    r = JobReport(job_id="broken-job", outcome=Outcome.SUSPENDED, surface=False,
                  overdue="", ran=0, deferred=0, expired=0, consecutive_failures=10)
    out = format_digest([r])
    assert "broken-job auto-suspended after 10 consecutive failures" in out
    assert "ccsched enable broken-job" in out
    assert "fires.jsonl" in out
```

Read `tests/scheduler/test_surface.py` first for its exact ledger-entry-building
helper style, then add (adapting names to match):

```python
def test_suspend_event_surfaces_as_suspended_report(monkeypatch, tmp_path):
    # ... seed a SUSPEND ledger entry the same way the file's existing
    # FAIL-event test seeds a FAIL entry, then assert:
    result = surface.surface(session_uuid="s1")
    assert result.reports[-1].outcome == Outcome.SUSPENDED
    assert result.reports[-1].consecutive_failures == 10
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_digest.py tests/scheduler/test_surface.py -q`
Expected: FAIL — `Outcome.SUSPENDED` doesn't exist; SUSPEND events aren't surfaced.

- [ ] **Step 3: Implement**

In `digest.py`:

```python
class Outcome(str, Enum):
    RAN = "ran"
    FAILED = "failed"
    LAUNCHED = "launched"
    SUSPENDED = "suspended"
```

In `_line()`, add a branch before the `FAILED` check (order doesn't matter since
they're mutually exclusive per-report, but keep it visually grouped with FAILED):

```python
def _line(report: JobReport) -> str | None:
    if report.outcome is Outcome.SUSPENDED:
        return (
            f"⛔ {report.job_id} auto-suspended after "
            f"{report.consecutive_failures} consecutive failures — see fires.jsonl / "
            f"run `ccsched enable {report.job_id}` after fixing"
        )
    if report.outcome is Outcome.FAILED:
        ...
```

Note this always surfaces (no `report.surface` check), matching how FAILED already
bypasses the per-job `surface` flag — a suspend event is exactly the kind of thing
a "silent" job must not be allowed to hide.

In `surface.py`:

```python
_RAN_EVENTS = {ledger.LedgerEvent.RUN.value, ledger.LedgerEvent.BACKFILL.value}
_FAIL_EVENTS = {ledger.LedgerEvent.FAIL.value}
_LAUNCH_EVENTS = {ledger.LedgerEvent.LAUNCH.value}
_SUSPEND_EVENTS = {ledger.LedgerEvent.SUSPEND.value}
```

```python
        elif event in _SUSPEND_EVENTS:
            raw_cf = e.get("consecutive_failures")
            consecutive = int(raw_cf) if isinstance(raw_cf, int) else 0
            reports.append(JobReport(
                job_id=job_id, outcome=Outcome.SUSPENDED,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=0, deferred=0, expired=0, consecutive_failures=consecutive,
            ))
```

(Add this `elif` branch alongside the existing `_FAIL_EVENTS`/`_RAN_EVENTS`/
`_LAUNCH_EVENTS` branches in the same `for e in entries:` loop.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_digest.py tests/scheduler/test_surface.py -q`
Expected: PASS, all tests including pre-existing ones.

- [ ] **Step 5: mypy**

Run: `uv run mypy --strict src/cc_session_tools/lib/scheduler/digest.py src/cc_session_tools/lib/scheduler/surface.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/digest.py src/cc_session_tools/lib/scheduler/surface.py \
        tests/scheduler/test_digest.py tests/scheduler/test_surface.py
git commit -m "feat(scheduler): loud, always-surfaced digest line for auto-suspend"
```

---

### Task 7: `ccsched enable` clears suspension; `ccsched run` stops clobbering it

**Files:**
- Modify: `src/cc_session_tools/cli/ccsched.py`
- Test: `tests/scheduler/test_ccsched_cli.py`

Two related fixes in the CLI layer:

1. `ccsched enable <job>` is the documented recovery path in every message this
   feature produces (`notify.suspended`, `digest.py`'s SUSPENDED line) — it must
   actually clear the flag, or those messages are lying to the user.
2. `_cmd_run`'s existing failure/success branches reconstruct `JobState` from
   scratch without listing `suspended` — since `suspended` now defaults to
   `False`, *every* manual `ccsched run <id>` (success or failure) would silently
   un-suspend a suspended job as an unintended side effect of adding the field.
   This must be fixed as part of adding the field, not left as a latent bug.

- [ ] **Step 1: Write the failing tests**

Add to `tests/scheduler/test_ccsched_cli.py`:

Add this import alongside the file's existing imports at module top level (every
other test in this file imports the scheduler lib the same way — `pyproject.toml`
sets `pythonpath = ["src"]` under `[tool.pytest.ini_options]`, so no `sys.path`
hack is needed or wanted):

```python
from cc_session_tools.lib.scheduler import state as st
```

```python
def test_enable_clears_suspension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    st.save_all_state({"tesco": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    assert _run(["enable", "tesco"], sched, hooks).returncode == 0
    assert st.load_all_state()["tesco"].suspended is False


def test_run_does_not_clear_existing_suspension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    st.save_all_state({"tesco": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    assert _run(["run", "tesco"], sched, hooks).returncode == 0  # `true` succeeds
    assert st.load_all_state()["tesco"].suspended is True  # still suspended
```

`monkeypatch.setenv` only affects this *test* process's own `os.environ`; `_run()`
builds the subprocess's `env` dict explicitly via `dict(os.environ)` plus its own
overrides (see the top of the file), so the setenv value flows through to the
subprocess with no interference, and monkeypatch auto-restores at teardown — no
manual backup/restore code needed. `test_ccsched_cli.py` does not currently import
`pytest` at module level (it only uses bare functions with `Path` params) — add
`import pytest` alongside the existing `from pathlib import Path` import so the
`monkeypatch: pytest.MonkeyPatch` fixture type-hints resolve.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_ccsched_cli.py -q`
Expected: FAIL — `enable` doesn't clear suspension; `run` clobbers it.

- [ ] **Step 3: Implement**

In `ccsched.py`:

```python
from cc_session_tools.lib.scheduler import (
    cursor,
    ledger,
    reconcile,
    registry,
    state,
    surface,
    worker,
)
```

(already imports `state` — no import change needed here.)

```python
def _cmd_set_enabled(job_id: str, enabled: bool) -> int:
    try:
        registry.set_enabled(job_id, enabled)
    except registry.RegistryError as exc:
        return _err(str(exc))
    if enabled:
        state.clear_suspended(job_id)
    print(f"{'enabled' if enabled else 'disabled'} {job_id}")
    return 0
```

In `_cmd_run`, both `JobState(...)` field lists gain `suspended=js.suspended`:

```python
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
        suspended=js.suspended,
    )
    state.save_all_state(states)
    ...
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_ccsched_cli.py -q`
Expected: PASS, all tests including pre-existing ones.

- [ ] **Step 5: mypy**

Run: `uv run mypy --strict src/cc_session_tools/cli/ccsched.py`
Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/cli/ccsched.py tests/scheduler/test_ccsched_cli.py
git commit -m "fix(ccsched): enable clears auto-suspension; run no longer clobbers it"
```

---

### Task 8: Full verification sweep + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the entire suite**

Run: `uv run pytest -q`
Expected: all tests pass, including everything outside `tests/scheduler/` (nothing
else should be touched by this change, but a full run confirms it).

- [ ] **Step 2: Run mypy strict over the whole touched surface**

Run:
```bash
uv run mypy --strict \
  src/cc_session_tools/lib/scheduler/state.py \
  src/cc_session_tools/lib/scheduler/notify.py \
  src/cc_session_tools/lib/scheduler/ledger.py \
  src/cc_session_tools/lib/scheduler/worker.py \
  src/cc_session_tools/lib/scheduler/reconcile.py \
  src/cc_session_tools/lib/scheduler/digest.py \
  src/cc_session_tools/lib/scheduler/surface.py \
  src/cc_session_tools/cli/ccsched.py
```
Expected: `Success: no issues found`

- [ ] **Step 3: Update CHANGELOG.md**

Check the current state of `## [Unreleased]` first — as of this plan being
written, the 2026-07-09 cursor-seeding fix that used to live under
`## [Unreleased]` → `### Fixed` has already been cut into a released
`## [0.17.0] - 2026-07-09` section by PR #68, so `## [Unreleased]` is now empty
with no `### Fixed` heading. Create a fresh one:

```markdown
## [Unreleased]

### Fixed

- **`ccsched` jobs now auto-suspend after 10 consecutive failures instead of
  storm-retrying forever.** A misconfigured job (e.g. the `ccmsg-dead-letter-sweep`
  incident on 2026-06-27 — 153 consecutive failures over ~2h43m before a human
  noticed) had no backoff: `reconcile_and_launch()` relaunched it on every
  `SessionStart`/throttled `UserPromptSubmit` regardless of how many times it had
  already failed. The detached worker now flips a new `suspended` flag in
  `state.json` once `consecutive_failures` reaches 10, `reconcile_and_launch()`
  skips suspended jobs, and a one-time Telegram push (`notify.py`, direct Bot API
  call — no live session required) fires at the moment of suspension so a
  permanently broken job in a rarely-opened project doesn't go unnoticed. Run
  `ccsched enable <job>` after fixing the job to clear the suspension and resume.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): document ccsched auto-suspend fix"
```

---

### Task 9: Hand back for review

- [ ] Push the branch: `git push -u origin f/20260710-ccsched-backoff`
- [ ] Use `superpowers:finishing-a-development-branch` to decide merge/PR path with
      the user — do not merge or open a PR without that checkpoint.
