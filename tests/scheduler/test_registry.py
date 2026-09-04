from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import store
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
    assert loaded[0].surface is True
    assert loaded[0].enabled is True
    assert loaded[0].catchup_window == "7d"
    assert loaded[0].timeout == "60s"
    assert loaded[0].success_exit_codes == (0,)


def test_success_exit_codes_round_trips_through_add_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(validate_job_fields(
        job_id="drift", cadence="daily@09:00", coalesce="one", command=["x"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
        success_exit_codes=(0, 1),
    ))
    assert reg.load_registry()[0].success_exit_codes == (0, 1)
    reg.replace_job(validate_job_fields(
        job_id="drift", cadence="daily@09:00", coalesce="one", command=["x"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
        success_exit_codes=(0,),
    ))
    assert reg.load_registry()[0].success_exit_codes == (0,)


def test_add_duplicate_id_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    with pytest.raises(reg.RegistryError):
        reg.add_job(_spec())


def test_load_preserves_insertion_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    for jid in ("c", "a", "b"):
        reg.add_job(_spec(jid))
    assert [s.job_id for s in reg.load_registry()] == ["c", "a", "b"]
    # An edit keeps position; a remove+re-add moves to the end.
    reg.replace_job(_spec("a"))
    assert [s.job_id for s in reg.load_registry()] == ["c", "a", "b"]
    reg.remove_job("a")
    reg.add_job(_spec("a"))
    assert [s.job_id for s in reg.load_registry()] == ["c", "b", "a"]


def test_replace_unknown_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with pytest.raises(reg.RegistryError):
        reg.replace_job(_spec("ghost"))


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


def test_set_enabled_unknown_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with pytest.raises(reg.RegistryError):
        reg.set_enabled("ghost", False)


def test_concurrent_edits_to_different_jobs_all_land(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: N threads each editing a DIFFERENT job must all persist — no silent
    last-write-wins loss (the whole-file jobs.toml RMW would drop most of these)."""
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    ids = [f"job-{i}" for i in range(16)]
    for jid in ids:
        reg.add_job(_spec(jid))

    errors: list[Exception] = []

    def flip(jid: str) -> None:
        try:
            reg.set_enabled(jid, False)
        except Exception as exc:  # noqa: BLE001 - captured for assertion
            errors.append(exc)

    threads = [threading.Thread(target=flip, args=(jid,)) for jid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    disabled = {s.job_id: s.enabled for s in reg.load_registry()}
    assert all(disabled[jid] is False for jid in ids)  # every edit landed


# ---------- bundled-job install history ----------


def test_bundled_install_ids_is_empty_before_any_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert reg.bundled_install_ids() == set()


def test_mark_bundled_installed_is_visible_in_bundled_install_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.mark_bundled_installed("pdata-verify-all", "2026-08-27T00:00:00Z")
    assert reg.bundled_install_ids() == {"pdata-verify-all"}


def test_mark_bundled_installed_survives_a_later_remove_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of this table: ccsched remove deletes the `jobs` row, but the install
    history is a separate table, so a machine that once installed a bundled job and later
    removed it is still distinguishable from one that never installed it at all."""
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec("pdata-verify-all"))
    reg.mark_bundled_installed("pdata-verify-all", "2026-08-27T00:00:00Z")
    reg.remove_job("pdata-verify-all")
    assert reg.load_registry() == []
    assert reg.bundled_install_ids() == {"pdata-verify-all"}


def test_mark_bundled_installed_twice_does_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.mark_bundled_installed("pdata-verify-all", "2026-08-27T00:00:00Z")
    reg.mark_bundled_installed("pdata-verify-all", "2026-08-28T00:00:00Z")
    assert reg.bundled_install_ids() == {"pdata-verify-all"}


# ---------- rename_job ----------


def test_rename_updates_job_id_preserving_other_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec("old-name"))
    reg.rename_job("old-name", "new-name")
    loaded = {s.job_id: s for s in reg.load_registry()}
    assert set(loaded) == {"new-name"}
    assert loaded["new-name"].cadence == "daily@09:00"
    assert loaded["new-name"].command == ("ccst", "hooks", "run", "check-tesco-due")


def test_rename_unknown_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with pytest.raises(reg.RegistryError):
        reg.rename_job("ghost", "new-name")


def test_rename_to_existing_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec("old-name"))
    reg.add_job(_spec("taken"))
    with pytest.raises(reg.RegistryError):
        reg.rename_job("old-name", "taken")
    # Neither job was touched by the failed rename.
    assert {s.job_id for s in reg.load_registry()} == {"old-name", "taken"}


def test_rename_also_renames_job_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cc_session_tools.lib.scheduler import state as st

    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec("old-name"))
    from datetime import datetime, timezone

    st.ensure_registered_db("old-name", datetime(2026, 6, 20, tzinfo=timezone.utc))
    st.record_success("old-name", new_success="2026-06-20T10:00:00Z", attempt_ts="2026-06-20T10:00:00Z")
    reg.rename_job("old-name", "new-name")
    states = st.load_all_state()
    assert "old-name" not in states
    assert states["new-name"].last_success == "2026-06-20T10:00:00Z"


def test_rename_also_renames_bundled_install_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec("pdata-verify-all"))
    reg.mark_bundled_installed("pdata-verify-all", "2026-08-27T00:00:00Z")
    reg.rename_job("pdata-verify-all", "pdata-verify-all-v2")
    assert reg.bundled_install_ids() == {"pdata-verify-all-v2"}


# ---------- created_at / updated_at / version (CAS) ----------


def _row(job_id: str) -> dict:
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT created_at, updated_at, version FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row)


def test_add_job_sets_created_at_and_default_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    row = _row("tesco-shop-check")
    assert row["created_at"] is not None
    assert row["updated_at"] is None  # not yet updated, only inserted
    assert row["version"] == 1


def test_replace_job_bumps_updated_at_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    reg.replace_job(_spec())  # default version=1 matches the just-added row
    row = _row("tesco-shop-check")
    assert row["updated_at"] is not None
    assert row["version"] == 2


def test_replace_job_with_stale_version_raises_conflict_not_silently_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The concurrent-edit scenario this whole CAS wiring exists for: two `ccsched edit`
    invocations both read the job at version 1, both compute an edit, both call replace_job.
    The first wins and advances to version 2; the second must be rejected, not silently
    overwrite the first edit."""
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    from dataclasses import replace as spec_replace

    winner = spec_replace(_spec(), timeout="90s")
    reg.replace_job(winner)  # version 1 -> 2

    loser = spec_replace(_spec(), timeout="120s")  # still thinks it's version 1
    with pytest.raises(reg.JobVersionConflictError):
        reg.replace_job(loser)

    # The winner's write is intact - the loser's did not silently apply.
    assert reg.load_registry()[0].timeout == "90s"


def test_replace_job_unknown_id_raises_registry_error_not_version_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with pytest.raises(reg.RegistryError) as exc_info:
        reg.replace_job(_spec("ghost"))
    assert not isinstance(exc_info.value, reg.JobVersionConflictError)


def test_set_enabled_bumps_updated_at_not_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    reg.set_enabled("tesco-shop-check", False)
    row = _row("tesco-shop-check")
    assert row["updated_at"] is not None
    assert row["version"] == 1  # not a CAS-guarded write path


def test_rename_job_bumps_updated_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec("old-name"))
    reg.rename_job("old-name", "new-name")
    row = _row("new-name")
    assert row["updated_at"] is not None


def test_mark_bundled_installed_created_at_survives_reinstall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.mark_bundled_installed("pdata-verify-all", "2026-08-27T00:00:00Z")
    conn = store.connect()
    try:
        first_created_at = conn.execute(
            "SELECT created_at FROM bundled_job_installs WHERE job_id=?",
            ("pdata-verify-all",),
        ).fetchone()["created_at"]
    finally:
        conn.close()
    assert first_created_at is not None

    reg.mark_bundled_installed("pdata-verify-all", "2026-08-28T00:00:00Z")  # reinstall
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT created_at, installed_at FROM bundled_job_installs WHERE job_id=?",
            ("pdata-verify-all",),
        ).fetchone()
    finally:
        conn.close()
    assert row["created_at"] == first_created_at  # preserved across the reinstall
    assert row["installed_at"] == "2026-08-28T00:00:00Z"  # this column still updates
