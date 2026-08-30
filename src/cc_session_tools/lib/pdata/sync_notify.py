"""Wire a pdata sync conflict (FORK / CHECKSUM_INVALID from `rehydrate.rehydrate`, or the
equivalent case from `dump.write_latest`) into the two notification channels that already exist
in this codebase — building neither channel, only feeding both from one call.

Channel 1, Telegram: `cc_session_tools.lib.scheduler.notify.send_telegram` — the same mechanism
the interactive `notify-user` skill uses (per that module's own docstring), reachable here because
a conflict can be discovered from contexts with no LLM in the loop (a CLI command, a detached
`ccsched _run-job` worker). It degrades to a logged warning and `False` on any failure and never
raises, so a broken Telegram path can never break the caller reporting the conflict.

Channel 2, the SessionStart catch-up digest: fed by writing a row to the same `catchup_events`
ledger (`cc_session_tools.lib.scheduler.ledger.record`) that every bundled `ccsched` job already
writes to, so a conflict is picked up by the existing `surface.surface()` / `digest.format_digest()`
pipeline (the machinery behind `[cc-scheduler] scheduled-task catch-up: ...`) with no second
digest-plumbing path to maintain.

This deliberately does NOT reuse the other SessionStart mechanism this repo has —
`cccs_hooks.pending_rename`'s dedicated `.pending-rename`-marker bash hook. That mechanism is not
a general-purpose "digest queue" a second caller can feed; it is a bespoke marker file plus a
bespoke bash script checking exactly that one file, with no reusable write-side API at all —
using it here would mean building a third channel (a new marker format, a new SessionStart hook
script), which is exactly the "not building either channel" scope this module is meant to stay
inside. It would also be redundant for the case it might otherwise justify itself for: a live
session that just hit a conflict already gets an immediate, in-the-moment
`hookSpecificOutput`/`systemMessage` straight from the calling hook (see Task 12's
`cccs_hooks.pdata_sync`, which prints its own message using the same JSON protocol as
`catchup.py`'s `_emit`, exactly like `pending_rename.py` does) — no digest round-trip needed.

The ledger *is* still the right channel for the one case that genuinely needs it: a conflict
discovered by Task 13's hourly bundled `ccsched` job, with no session open to print anything live.
Recording it as a `LedgerEvent.RUN` row with a non-zero `exit_code` and the conflict detail in
`error` is a real fit, not a shoehorn: `surface.py`'s RAN branch already exists precisely for "a
completed [bundled job] run's non-zero-exit captured stdout" (see its own module docstring, and
`JobReport.findings`'s "e.g. a drift monitor's 'found something' report") — a pdata conflict
discovered by the hourly sync-check job is exactly that shape, and renders as
`⚠ pdata-sync:<project> ran with findings: <outcome>: <detail>` with the full detail text intact.
`LedgerEvent.FAIL` was considered and rejected: `digest.py`'s FAILED line never includes the
`error` column at all (only a bare "failed (Nth consecutive) — see `ccsched status <job_id>`"),
which would silently drop `detail` from the very digest line this module exists to produce, and
would point the user at `ccsched status`/`ccsched enable` — commands for a *registered* job this
synthetic `job_id` never is. Per this repo's coding standards' warning against forcing a
non-job event through a job-shaped record, `LedgerEvent.FAIL` is the actual shoehorn here;
`LedgerEvent.RUN`-with-findings is the one row shape `surface.py`/`digest.py` already model this
kind of "ran and found something worth telling you" event with.
"""
from __future__ import annotations

from collections.abc import Callable

from cc_session_tools.lib.scheduler.ledger import LedgerEntry, LedgerEvent, record
from cc_session_tools.lib.scheduler.notify import send_telegram

# Type of `send_telegram` (module-level, monkeypatchable by tests the same way
# `notify.Poster` is - see this module's own docstring for why the underlying function itself
# is not reimplemented here).
TelegramSend = Callable[[str], bool]

# job_id prefix for the synthetic ledger rows this module writes. Namespaced so it can never
# collide with a real registered ccsched job_id, and so `ccsched status pdata-sync:<project>`
# reads unambiguously as "history for this project's pdata sync", not a registered job.
_JOB_ID_PREFIX = "pdata-sync:"


def notify_conflict(project: str, *, outcome: str, detail: str) -> None:
    """Push a pdata sync conflict into both existing notification channels. Never raises: both
    `send_telegram` and `ledger.record` already degrade to a logged warning on failure, and this
    function adds no additional failure mode on top — a notification that can't be sent must
    never break the CLI command, hook, or cron job it is reporting from."""
    message = f"[pdata-sync] {project}: {outcome} — {detail}"
    send_telegram(message)
    record(LedgerEntry(
        job_id=f"{_JOB_ID_PREFIX}{project}",
        event=LedgerEvent.RUN,
        owed=0,
        ran=1,
        exit_code=1,
        duration_ms=0,
        error=f"{outcome}: {detail}",
    ))
