# tests/messaging/test_repository.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import repository as repo
from cc_session_tools.lib.messaging import store


def test_connect_creates_ccmsg_db_with_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    conn = repo.connect()
    try:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert {"messages", "cursors"} <= names
    assert mode.lower() == "wal"
    assert (tmp_path / "ccmsg.db").exists()


from cc_session_tools.lib.messaging.message import Message


def _msg(mid: str, **over) -> Message:
    base = dict(
        id=mid, schema=1, from_project="oneshot", from_session="20260615-x",
        from_uuid="sender-uuid", to_kind="project", to_value="alpha",
        to_location="projects/alpha", subject="Hello there",
        sent_at="2026-06-20T00:00:00Z", status="sent", read_at=None,
        read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=["/abs/a.md"], body="Body.",
    )
    base.update(over)
    return Message(**base)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    return tmp_path


def test_insert_then_get_round_trips_all_fields(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001"))
    got = repo.get_by_id("20260620T000000Z-0001")
    assert got is not None
    assert got.subject == "Hello there"
    assert got.attachments == ["/abs/a.md"]
    assert got.receipt_shown is False
    assert got.read_at is None


def test_get_missing_returns_none(root: Path) -> None:
    assert repo.get_by_id("nope") is None


def test_list_rows_filters(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", from_uuid="u1", to_location="projects/alpha"))
    repo.insert(_msg("20260620T000000Z-0002", from_uuid="u2", to_location="projects/beta",
                     to_value="beta", status="read", read_at="2026-06-20T01:00:00Z"))
    assert len(repo.list_rows()) == 2
    assert len(repo.list_rows(status="sent")) == 1
    assert len(repo.list_rows(partition="projects/beta")) == 1
    assert len(repo.list_rows(from_uuid="u1")) == 1


def test_sweep_new_respects_high_water(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", to_location="projects/alpha"))
    repo.insert(_msg("20260620T120000Z-0002", to_location="projects/alpha"))
    repo.insert(_msg("20260620T000000Z-0003", to_location="_global", to_value="x",
                     to_kind="description"))
    swept = repo.sweep_new(["projects/alpha", "_global"],
                           {"projects/alpha": "20260620T000000Z-0001"})
    ids = [m.id for m in swept]
    assert ids == ["20260620T120000Z-0002", "20260620T000000Z-0003"]


def test_mark_read_is_first_writer_wins(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", to_kind="project"))
    assert repo.mark_read("20260620T000000Z-0001", "uuid-A", "2026-06-20T02:00:00Z", "projA") is True
    # Second attempt: row is already 'read', so it stamps nothing and returns False.
    assert repo.mark_read("20260620T000000Z-0001", "uuid-B", "2026-06-20T03:00:00Z", "projB") is False
    got = repo.get_by_id("20260620T000000Z-0001")
    assert got is not None
    assert got.status == "read"
    assert got.read_by_uuid == "uuid-A"   # first writer won
    assert got.read_by_session == "projA"


from cc_session_tools.lib.messaging.lock import AlreadyClaimedError


def test_claim_flips_and_stamps(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", to_kind="description", to_value="X",
                     to_location="_global"))
    m = repo.claim("20260620T000000Z-0001", "me-uuid", "beta", "2026-06-20T05:00:00Z")
    assert m.status == "claimed"
    assert m.read_by_uuid == "me-uuid"
    assert m.claimed_at == "2026-06-20T05:00:00Z"
    assert m.read_at == "2026-06-20T05:00:00Z"  # back-filled from now


def test_claim_missing_raises_not_found(root: Path) -> None:
    with pytest.raises(repo.MessageNotFoundError):
        repo.claim("ghost", "u", "s", "2026-06-20T05:00:00Z")


def test_second_claim_raises_already_claimed(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", to_kind="description", to_value="X",
                     to_location="_global"))
    repo.claim("20260620T000000Z-0001", "me", "s", "2026-06-20T05:00:00Z")
    with pytest.raises(AlreadyClaimedError):
        repo.claim("20260620T000000Z-0001", "other", "s2", "2026-06-20T06:00:00Z")
