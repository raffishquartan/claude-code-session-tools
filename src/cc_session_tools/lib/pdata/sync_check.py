"""The hourly `ccsched` job's per-project algorithm (spec: "Triggers", the "Hourly `ccsched` job"
row): rehydrate-check first, then - only if no rehydrate happened - dump-check.

This is deliberately its own subcommand (`ccst pdata sync-check`) rather than the existing
`ccst pdata rehydrate --all-projects` and `ccst pdata dump --all-projects` run as two bundled
jobs. Two reasons, both structural rather than cosmetic:

- `_cmd_pdata_dump`'s CLI path does not call `dump.is_no_op_publish()`, on purpose: an *explicit*
  `ccst pdata dump` should still let a human republish an unchanged dump to refresh its header.
  Run hourly and unattended, that same behaviour means a `write_latest()` - a real archive copy,
  a rewritten `latest.sql`/`latest.sha256`, and (since `.pdata-db-dump/` lives under a synced
  folder) real OneDrive traffic - for every idle project, every hour, forever. This module is the
  automatic, repeating caller `is_no_op_publish()`'s own docstring was written for.
- Two independent jobs cannot express "dump-check *only if* the rehydrate-check didn't act",
  which is what the spec's hourly row actually specifies. Getting that ordering wrong is not
  harmless - see the DEFERRED and OCCUPIED cases below, where a dump-check run in isolation
  reports a false conflict.

Every conflict is pushed through `sync_notify.notify_conflict()` from here, not from the CLI
handler: with no session open, the notification *is* the only way the user finds out, so it must
fire regardless of which caller invoked the check.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib import machine_identity, occupancy
from cc_session_tools.lib.pdata import (
    dump,
    rehydrate,
    repository,
    store,
    sync_notify,
    vector_clock_store,
)


class SyncOutcome(enum.Enum):
    FAST_FORWARDED = "fast_forwarded"  # adopted the published dump; nothing else to do
    PUBLISHED = "published"            # local had new writes; a fresh dump was written
    UNCHANGED = "unchanged"            # local and the dump already agree; nothing written
    CONFLICT = "conflict"              # surfaced via notify_conflict; nothing written
    DEFERRED = "deferred"              # another writer holds the lock right now - retry next cycle
    OCCUPIED = "occupied"              # a live session is working in this project - skipped


@dataclass(frozen=True, slots=True)
class SyncCheckResult:
    outcome: SyncOutcome
    from_machine: str | None = None
    # The machine_id stamped into the dump this cycle published. Set on PUBLISHED only.
    machine_id: str | None = None
    # For CONFLICT: the underlying vector-clock/rehydrate outcome value ("fork",
    # "checksum_invalid", "dump_dominates") and the human wording already sent to
    # notify_conflict, so a caller can print exactly what the notification said rather than
    # inventing a fourth copy of the same message.
    conflict_outcome: str | None = None
    detail: str | None = None


def check_project(project: str) -> SyncCheckResult:
    """Run one full sync cycle for one project. Raises ValueError on an invalid project name or
    an unreadable store, matching `rehydrate.rehydrate()`/`repository.connect()`; every other
    outcome is reported through the returned result."""
    project_root = store.project_root(project)  # also validates the project name
    existing = dump.read_latest(project_root)

    if existing.present:
        decided = _rehydrate_half(project, project_root=project_root, existing=existing)
        if decided is not None:
            return decided
    # Either nothing has ever been published for this project (its first dump is exactly what
    # should happen now), or the rehydrate-check found local already at or ahead of the dump.
    return _dump_half(project, project_root=project_root, existing=existing)


def _rehydrate_half(
    project: str, *, project_root: Path, existing: dump.DumpInfo,
) -> SyncCheckResult | None:
    """The spec's step (1). `None` means "no rehydrate happened and the dump-check is safe to
    run" - every other return value is terminal for this project this cycle."""
    if not existing.checksum_valid:
        # A dump exists and cannot be trusted. Reported rather than published over: the recovery
        # path is a deliberate `ccst pdata dump --force` from the machine with good data, and an
        # unattended job must not make that choice on a human's behalf.
        return _conflict(
            project,
            outcome=rehydrate.RehydrateOutcome.CHECKSUM_INVALID.value,
            detail=rehydrate.conflict_detail(
                rehydrate.RehydrateResult(outcome=rehydrate.RehydrateOutcome.CHECKSUM_INVALID),
                project=project,
            ),
        )

    if occupancy.is_occupied(project_root):
        # Spec "Process safety": the hourly job, like SessionStart, never swaps a project's .db
        # out from under a live session. No exclude_pid, unlike the SessionStart hook - the
        # detached `ccsched _run-job` worker this runs in is not itself a `claude` process, so
        # there is nothing of its own to exclude.
        #
        # Terminal, not a fall-through to the dump-check: skipping the rehydrate leaves the
        # local-vs-dump relationship unestablished, so a bare dump-check could legitimately come
        # back DUMP_DOMINATES and get reported as a conflict every hour for as long as the
        # session stays open. Nothing is lost - the SessionEnd hook dumps that project when the
        # session closes, and the next cycle then covers it normally.
        return SyncCheckResult(outcome=SyncOutcome.OCCUPIED)

    result = rehydrate.rehydrate(project)  # never force: an automatic trigger must not override
    if result.outcome is rehydrate.RehydrateOutcome.FAST_FORWARDED:
        # No dump-check afterwards: a fast-forward makes local's vector exactly equal to the
        # published one, so `is_no_op_publish()` would immediately report "nothing new" anyway.
        return SyncCheckResult(
            outcome=SyncOutcome.FAST_FORWARDED, from_machine=result.from_machine,
        )
    if result.outcome in (
        rehydrate.RehydrateOutcome.FORK, rehydrate.RehydrateOutcome.CHECKSUM_INVALID,
    ):
        return _conflict(
            project,
            outcome=result.outcome.value,
            detail=rehydrate.conflict_detail(result, project=project),
        )
    if result.outcome is rehydrate.RehydrateOutcome.DEFERRED:
        # Terminal, and deliberately NOT a conflict. rehydrate() only reaches its lock check
        # after compare() has already returned DUMP_DOMINATES (LOCAL_DOMINATES returns NO_OP and
        # FORK returns FORK first), so DEFERRED is a proof that the published dump strictly
        # dominates local. Falling through to the dump-check would make decide_publish()
        # re-derive that same DUMP_DOMINATES and push a "conflict" notification, every hour, for
        # what is ordinary transient lock contention - exactly what `_cmd_pdata_rehydrate`'s own
        # DEFERRED branch already refuses to misdescribe. The next cycle retries.
        return SyncCheckResult(outcome=SyncOutcome.DEFERRED)
    return None  # NO_OP - local is at or ahead of the dump, so the dump-check is the right step


def _dump_half(
    project: str, *, project_root: Path, existing: dump.DumpInfo,
) -> SyncCheckResult:
    """The spec's step (2) - SessionEnd's rule, plus the no-op skip that makes it safe to repeat
    hourly. `existing` is the same DumpInfo the rehydrate-check used, so `decide_publish` and
    `is_no_op_publish` cannot disagree about what is currently published."""
    conn = repository.connect(project)
    try:
        local_vector = vector_clock_store.read_vector(conn)
        comparison = dump.decide_publish(local_vector=local_vector, existing=existing)
        if comparison is not None:
            # Reachable only when the published dump changed between the rehydrate-check's read
            # and this one (a sync delivery mid-cycle) - rare, but a genuine conflict, unlike the
            # DEFERRED/OCCUPIED cases above.
            return _conflict(
                project, outcome=comparison.value, detail=dump.refusal_detail(project),
            )
        if dump.is_no_op_publish(local_vector=local_vector, existing=existing):
            return SyncCheckResult(outcome=SyncOutcome.UNCHANGED)
        machine_id = machine_identity.resolve().machine_id
        dump.write_latest(
            conn, project_root=project_root, machine_id=machine_id, vector=local_vector,
        )
    finally:
        conn.close()
    return SyncCheckResult(outcome=SyncOutcome.PUBLISHED, machine_id=machine_id)


def _conflict(project: str, *, outcome: str, detail: str) -> SyncCheckResult:
    sync_notify.notify_conflict(project, outcome=outcome, detail=detail)
    return SyncCheckResult(
        outcome=SyncOutcome.CONFLICT, conflict_outcome=outcome, detail=detail,
    )
