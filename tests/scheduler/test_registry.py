from __future__ import annotations

import threading
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
