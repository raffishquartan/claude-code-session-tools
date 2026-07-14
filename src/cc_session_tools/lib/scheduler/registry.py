"""jobs registry, backed by the `jobs` table in ccsched.db. Each mutator is a
single-row INSERT/UPDATE/DELETE inside its own transaction, so concurrent edits
to different jobs never silently clobber each other (R1) — unlike the old
whole-file jobs.toml rewrite. Rows are written already-validated at the CLI
boundary, so load builds JobSpec directly without re-validating."""
from __future__ import annotations

import json
import sqlite3

from cc_session_tools.lib.scheduler import store
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, JobSpec


class RegistryError(ValueError):
    """Raised for duplicate ids, unknown-id mutations, or an unreadable DB."""


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
    )


def load_registry() -> list[JobSpec]:
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT job_id, cadence, coalesce_kind, command, surface, enabled, "
            "catchup_window, timeout FROM jobs ORDER BY rowid"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        # A corrupt/unreadable ccsched.db surfaces here; wrap so the reconcile
        # boundary's `except RegistryError` still degrades the hook to a digest
        # warning instead of crashing the session.
        raise RegistryError(f"ccsched.db is unreadable: {exc}") from exc
    finally:
        conn.close()
    return [_spec_from_row(r) for r in rows]


def add_job(spec: JobSpec) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, cadence, coalesce_kind, command, surface, "
            "enabled, catchup_window, timeout) VALUES (?,?,?,?,?,?,?,?)",
            (
                spec.job_id, spec.cadence, spec.coalesce.value,
                json.dumps(list(spec.command)), int(spec.surface), int(spec.enabled),
                spec.catchup_window, spec.timeout,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise RegistryError(f"job id already exists: {spec.job_id!r}") from exc
    finally:
        conn.close()


def replace_job(spec: JobSpec) -> None:
    conn = store.connect()
    try:
        cur = conn.execute(
            "UPDATE jobs SET cadence=?, coalesce_kind=?, command=?, surface=?, "
            "enabled=?, catchup_window=?, timeout=? WHERE job_id=?",
            (
                spec.cadence, spec.coalesce.value, json.dumps(list(spec.command)),
                int(spec.surface), int(spec.enabled), spec.catchup_window,
                spec.timeout, spec.job_id,
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise RegistryError(f"unknown job id: {spec.job_id!r}")
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
            "UPDATE jobs SET enabled=? WHERE job_id=?", (int(enabled), job_id)
        )
        conn.commit()
        if cur.rowcount == 0:
            raise RegistryError(f"unknown job id: {job_id!r}")
    finally:
        conn.close()
