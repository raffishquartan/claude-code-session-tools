"""Resolving a `ccmsg send --to-session` reference to a full session uuid."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import sessions_db
from cc_session_tools.lib.messaging.session_ref import SessionRefError, resolve_session_ref

UUID_A = "a80f8695-1111-4222-8333-444444444444"
UUID_B = "a80f8695-9999-4222-8333-555555555555"
UUID_C = "0c1d2e3f-1111-4222-8333-666666666666"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "sessions.db"
    proj = Path("/repos/proj")
    sessions_db.ensure_session_row(proj, "20261005-example-tag", uuid=UUID_C, path=path)
    sessions_db.ensure_session_row(proj, "20261005-forked", uuid=UUID_A, path=path)
    sessions_db.ensure_session_row(proj, "20261005-forked", uuid=UUID_B, path=path)
    sessions_db.ensure_session_row(Path("/repos/other"), "20261006-shared", uuid=UUID_A, path=path)
    sessions_db.ensure_session_row(proj, "20261006-shared", uuid=UUID_A, path=path)
    return path


def _resolve(ref: str, db_path: Path) -> str:
    return resolve_session_ref(
        ref,
        find_by_name=lambda n: sessions_db.find_exact(n, path=db_path),
        find_by_prefix=lambda p: sessions_db.find_by_uuid_prefix(p, path=db_path),
    )


def test_canonical_uuid_passes_through_without_consulting_the_store(db_path: Path) -> None:
    unknown = "11111111-2222-4333-8444-555555555555"
    assert _resolve(unknown, db_path) == unknown


def test_canonical_uuid_is_lowercased(db_path: Path) -> None:
    assert _resolve(UUID_C.upper(), db_path) == UUID_C


def test_unique_name_resolves_to_full_uuid(db_path: Path) -> None:
    assert _resolve("20261005-example-tag", db_path) == UUID_C


def test_unique_prefix_resolves_to_full_uuid(db_path: Path) -> None:
    assert _resolve("0c1d2e3f", db_path) == UUID_C
    assert _resolve("0c1d2e3f-1111", db_path) == UUID_C


def test_exact_name_wins_over_a_prefix_reading(tmp_path: Path) -> None:
    path = tmp_path / "sessions.db"
    named = "11111111-0000-4000-8000-000000000001"
    other = "20261005-fade-4000-8000-000000000002"
    sessions_db.ensure_session_row(Path("/repos/p"), "20261005-fade", uuid=named, path=path)
    sessions_db.ensure_session_row(Path("/repos/p"), "20261005-other", uuid=other, path=path)
    assert _resolve("20261005-fade", path) == named


def test_same_uuid_under_two_project_dirs_counts_once(db_path: Path) -> None:
    assert _resolve("20261006-shared", db_path) == UUID_A


def test_fork_with_two_uuids_is_ambiguous_and_lists_candidates(db_path: Path) -> None:
    with pytest.raises(SessionRefError) as exc:
        _resolve("20261005-forked", db_path)
    text = str(exc.value)
    assert "matches 2 sessions" in text
    assert UUID_A[:8] in text and "20261005-forked" in text and "/repos/proj" in text


def test_prefix_shared_by_two_sessions_is_ambiguous(db_path: Path) -> None:
    with pytest.raises(SessionRefError, match="matches 2 sessions"):
        _resolve("a80f8695", db_path)


def test_unknown_name_reports_no_match(db_path: Path) -> None:
    with pytest.raises(SessionRefError, match="no session matches"):
        _resolve("20260101-no-such-session", db_path)


def test_unknown_prefix_reports_no_match(db_path: Path) -> None:
    with pytest.raises(SessionRefError, match="no session matches"):
        _resolve("deadbeef", db_path)


def test_missing_store_reports_no_match(tmp_path: Path) -> None:
    with pytest.raises(SessionRefError, match="no session matches"):
        _resolve("20261005-example-tag", tmp_path / "absent.db")


@pytest.mark.parametrize("ref", ["a80f", "target-uuid", "", "a80f869", "zzzzzzzz"])
def test_value_in_no_accepted_form_names_the_forms(ref: str, db_path: Path) -> None:
    with pytest.raises(SessionRefError, match="not a session uuid, a session name"):
        _resolve(ref, db_path)


def test_find_by_uuid_prefix_returns_every_matching_row(db_path: Path) -> None:
    rows = sessions_db.find_by_uuid_prefix("a80f8695", path=db_path)
    assert {r.uuid for r in rows} == {UUID_A, UUID_B}


def test_find_by_uuid_prefix_on_nonexistent_db_returns_empty(tmp_path: Path) -> None:
    assert sessions_db.find_by_uuid_prefix("a80f8695", path=tmp_path / "absent.db") == []
