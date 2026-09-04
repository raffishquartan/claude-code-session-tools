"""jobs registry, backed by the `jobs` table in ccsched.db. Each mutator is a
single-row INSERT/UPDATE/DELETE inside its own transaction, so concurrent edits
to different jobs never silently clobber each other (R1) — unlike the old
whole-file jobs.toml rewrite. Rows are written already-validated at the CLI
boundary, so load builds JobSpec directly without re-validating."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from cc_session_tools.lib import db
from cc_session_tools.lib.scheduler import store
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, JobSpec


class RegistryError(ValueError):
    """Raised for duplicate ids, unknown-id mutations, or an unreadable DB."""


class JobVersionConflictError(RegistryError):
    """Raised by replace_job() when the row's version has moved since the caller read it -
    another edit landed in between. Distinct from the unknown-id case (RegistryError itself),
    so a caller (e.g. the `ccsched edit` CLI command) can tell "no such job" apart from "someone
    else edited this job first" and report each differently."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _spec_from_row(row: sqlite3.Row) -> JobSpec:
    return JobSpec(
        job_id=row["job_id"],
        cadence=row["cadence"],
        coalesce=CoalesceKind(row["coalesce_kind"]),
        command=tuple(json.loads(row["command"])),
        surface=bool(row["surface"]),
        enabled=bool(row["enabled"]),
        catchup_window=row["catchup_window"],
        timeout=row["timeout"],
        success_exit_codes=tuple(json.loads(row["success_exit_codes"])),
        version=int(row["version"]),
    )


def load_registry() -> list[JobSpec]:
    try:
        conn = store.connect()
        try:
            rows = conn.execute(
                "SELECT job_id, cadence, coalesce_kind, command, surface, enabled, "
                "catchup_window, timeout, success_exit_codes, version "
                "FROM jobs ORDER BY rowid"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        # A corrupt/unreadable ccsched.db surfaces either at connect() (the WAL
        # pragma) or at the query; wrap so the reconcile boundary's
        # `except RegistryError` still degrades the hook to a digest warning
        # instead of crashing the session.
        raise RegistryError(f"ccsched.db is unreadable: {exc}") from exc
    return [_spec_from_row(r) for r in rows]


def add_job(spec: JobSpec) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, cadence, coalesce_kind, command, surface, "
            "enabled, catchup_window, timeout, success_exit_codes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                spec.job_id, spec.cadence, spec.coalesce.value,
                json.dumps(list(spec.command)), int(spec.surface), int(spec.enabled),
                spec.catchup_window, spec.timeout,
                json.dumps(list(spec.success_exit_codes)), _now_iso(),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise RegistryError(f"job id already exists: {spec.job_id!r}") from exc
    finally:
        conn.close()


def replace_job(spec: JobSpec) -> None:
    """Compare-and-swap update, guarded by spec.version (the version this spec was read at -
    see JobSpec.version). Rejects the write, rather than silently overwriting, if another edit
    landed on this job since spec was read - the read-then-edit-then-write gap `ccsched edit`
    has between its load_registry() read and this call."""
    conn = store.connect()
    try:
        ok = db.cas_update(
            conn,
            table="jobs",
            id_column="job_id",
            id_value=spec.job_id,
            version_column="version",
            expected_version=spec.version,
            set_clause=(
                "cadence=?, coalesce_kind=?, command=?, surface=?, enabled=?, "
                "catchup_window=?, timeout=?, success_exit_codes=?, updated_at=?, "
                "version=version+1"
            ),
            params=(
                spec.cadence, spec.coalesce.value, json.dumps(list(spec.command)),
                int(spec.surface), int(spec.enabled), spec.catchup_window,
                spec.timeout, json.dumps(list(spec.success_exit_codes)), _now_iso(),
            ),
        )
        conn.commit()
        if not ok:
            exists = conn.execute(
                "SELECT 1 FROM jobs WHERE job_id=?", (spec.job_id,)
            ).fetchone()
            if exists is None:
                raise RegistryError(f"unknown job id: {spec.job_id!r}")
            raise JobVersionConflictError(
                f"job {spec.job_id!r} was modified since it was read "
                f"(expected version {spec.version})"
            )
    finally:
        conn.close()


def remove_job(job_id: str) -> None:
    conn = store.connect()
    try:
        cur = conn.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise RegistryError(f"unknown job id: {job_id!r}")
    finally:
        conn.close()


def set_enabled(job_id: str, enabled: bool) -> None:
    conn = store.connect()
    try:
        cur = conn.execute(
            "UPDATE jobs SET enabled=?, updated_at=? WHERE job_id=?",
            (int(enabled), _now_iso(), job_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise RegistryError(f"unknown job id: {job_id!r}")
    finally:
        conn.close()


def rename_job(old_id: str, new_id: str) -> None:
    """Repoint a job's id everywhere it's a row key in ccsched.db - the `jobs`
    spec row, its `job_state` row (so registered_at/last_success/etc. carry
    over), and its `bundled_job_installs` row if it has one - in a single
    transaction. Callers that also want run history to follow (telemetry.db's
    catchup_events) call ledger.rename_job separately; the two stores are
    never truly atomic together, matching how the rest of this package treats
    them as independent."""
    conn = store.connect()
    try:
        now_iso = _now_iso()
        cur = conn.execute(
            "UPDATE jobs SET job_id=?, updated_at=? WHERE job_id=?",
            (new_id, now_iso, old_id),
        )
        if cur.rowcount == 0:
            conn.commit()
            raise RegistryError(f"unknown job id: {old_id!r}")
        conn.execute(
            "UPDATE job_state SET job_id=?, updated_at=? WHERE job_id=?",
            (new_id, now_iso, old_id),
        )
        conn.execute(
            "UPDATE bundled_job_installs SET job_id=? WHERE job_id=?", (new_id, old_id)
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise RegistryError(f"job id already exists: {new_id!r}") from exc
    finally:
        conn.close()


def mark_bundled_installed(job_id: str, installed_at: str) -> None:
    """Record that a CCST-bundled job (lib/scheduler/bundled_jobs.py) has been installed on this
    machine, so a later `ccsched remove` can be told apart from "never installed" by
    bundled_install_ids(). INSERT ... ON CONFLICT DO UPDATE (not OR REPLACE - REPLACE would
    reset created_at on a reinstall): called on every successful `ccst ccsched-jobs install
    --apply`, including a job already registered from before this table existed, so it
    self-backfills rather than needing a one-shot migration."""
    conn = store.connect()
    try:
        conn.execute(
            "INSERT INTO bundled_job_installs (job_id, installed_at, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET "
            "installed_at=excluded.installed_at",
            (job_id, installed_at, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def bundled_install_ids() -> set[str]:
    """Every bundled job id ever installed on this machine via
    `ccst ccsched-jobs install --apply`, whether or not it is still registered now."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT job_id FROM bundled_job_installs").fetchall()
    finally:
        conn.close()
    return {row["job_id"] for row in rows}
