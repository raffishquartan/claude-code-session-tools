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


def test_archive_one_flips_status(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", status="read",
                     read_at="2026-06-20T00:00:00Z"))
    m = repo.archive_one("20260620T000000Z-0001")
    assert m.status == "archived"


def test_archive_one_missing_raises(root: Path) -> None:
    with pytest.raises(repo.MessageNotFoundError):
        repo.archive_one("ghost")


def test_archive_aged_only_settled_older_than_cutoff(root: Path) -> None:
    # read 15 days ago -> archived; read 13 days ago -> stays; unread -> stays.
    repo.insert(_msg("20260101T000000Z-0001", status="read",
                     read_at="2026-06-05T00:00:00Z"))            # old
    repo.insert(_msg("20260101T000000Z-0002", status="read",
                     read_at="2026-06-17T00:00:00Z"))            # recent
    repo.insert(_msg("20260101T000000Z-0003", status="sent"))   # unread
    cutoff = "2026-06-06T00:00:00Z"  # now(2026-06-20) - 14d
    archived = repo.archive_aged("projects/alpha", cutoff)
    assert archived == ["20260101T000000Z-0001"]
    assert repo.get_by_id("20260101T000000Z-0002").status == "read"
    assert repo.get_by_id("20260101T000000Z-0003").status == "sent"


def test_archive_aged_uses_claimed_at_when_read_at_null(root: Path) -> None:
    repo.insert(_msg("20260101T000000Z-0004", to_kind="description", to_value="w",
                     to_location="projects/alpha", status="claimed", read_at=None,
                     claimed_at="2026-06-05T00:00:00Z"))
    assert repo.archive_aged("projects/alpha", "2026-06-06T00:00:00Z") == \
        ["20260101T000000Z-0004"]


def test_pending_receipts_only_unshown_read_or_claimed(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", from_uuid="me", status="read",
                     read_at="2026-06-20T00:00:00Z", read_by_session="rA"))
    repo.insert(_msg("20260620T000000Z-0002", from_uuid="me", status="sent"))
    repo.insert(_msg("20260620T000000Z-0003", from_uuid="other", status="read",
                     read_at="2026-06-20T00:00:00Z"))
    pending = repo.pending_receipts("me")
    assert [m.id for m in pending] == ["20260620T000000Z-0001"]


def test_mark_receipts_shown_is_idempotent(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", from_uuid="me", status="read",
                     read_at="2026-06-20T00:00:00Z"))
    repo.mark_receipts_shown(["20260620T000000Z-0001"])
    assert repo.pending_receipts("me") == []
    repo.mark_receipts_shown(["20260620T000000Z-0001"])  # no-op, no error


def test_cursor_load_save_round_trip(root: Path) -> None:
    assert repo.load_cursor("uuid-1") == {}
    repo.save_cursor("uuid-1", {"projects/alpha": "20260620T120000Z-0002"})
    assert repo.load_cursor("uuid-1") == {"projects/alpha": "20260620T120000Z-0002"}
    # upsert overwrites the same (uuid, partition)
    repo.save_cursor("uuid-1", {"projects/alpha": "20260620T130000Z-0003"})
    assert repo.load_cursor("uuid-1")["projects/alpha"] == "20260620T130000Z-0003"


def test_refresh_display_tags_updates_sender_and_reader(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", from_uuid="u", from_session="old"))
    repo.insert(_msg("20260620T000000Z-0002", from_uuid="z", read_by_uuid="u",
                     read_by_session="old", status="read",
                     read_at="2026-06-20T00:00:00Z"))
    repo.insert(_msg("20260620T000000Z-0003", from_uuid="u", from_session="old",
                     status="archived"))  # archived: untouched
    n = repo.refresh_display_tags("u", "new-tag")
    assert n == 2
    assert repo.get_by_id("20260620T000000Z-0001").from_session == "new-tag"
    assert repo.get_by_id("20260620T000000Z-0002").read_by_session == "new-tag"
    assert repo.get_by_id("20260620T000000Z-0003").from_session == "old"
