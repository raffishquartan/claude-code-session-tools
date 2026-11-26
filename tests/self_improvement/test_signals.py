"""Port of the config repository's collector bats suite, plus store-backed source tests."""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from cc_session_tools.lib.messaging import repository
from cc_session_tools.lib.messaging.message import Message
from cc_session_tools.lib.self_improvement import signals
from .conftest import Env, days_ago, iso_days_ago, tree_checksums

SENSITIVE_SUBJECT = "Injunction advice for Jane Sensitive-Doe"


def _message(mid: str, status: str, age: dt.timedelta, subject: str = "s") -> Message:
    sent = (dt.datetime.now(dt.timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location="projects/alpha",
        subject=subject, sent_at=sent, status=status,  # type: ignore[arg-type]
        read_at=None, read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    )


def seed_messages() -> None:
    """Two unclaimed messages older than 14 days (one with a sensitive subject), one recent
    unclaimed, one brand new, one old but claimed, one old but archived."""
    days = dt.timedelta(days=1)
    repository.insert(_message("20260801T000000Z-aaaa", "sent", 40 * days, SENSITIVE_SUBJECT))
    repository.insert(_message("20260802T000000Z-bbbb", "read", 20 * days))
    repository.insert(_message("20260803T000000Z-cccc", "read", 3 * days))
    repository.insert(_message("20260804T000000Z-dddd", "sent", dt.timedelta(hours=5)))
    repository.insert(_message("20260805T000000Z-eeee", "claimed", 90 * days))
    repository.insert(_message("20260806T000000Z-ffff", "archived", 90 * days))


def seed_denies() -> None:
    from hooks.telemetry import TelemetryEntry, log_event

    for hook, decision in (
        ("bash-hard-deny", "deny"), ("bash-hard-deny", "deny"), ("confirm-8digit", "deny"),
        ("bash-hard-deny", "allow"),
    ):
        log_event(TelemetryEntry(
            hook=hook, event="PreToolUse", tool="Bash", session_id="", cwd_short="",
            decision=decision, cache="none", verdict="blocked", input_hash="",
        ))


def write_reliability(si: Env) -> None:
    refs = si.home / ".claude" / "skills" / "demo" / "references"
    refs.mkdir(parents=True)
    (refs / "contract.md").write_text(
        "# Reliability log\n\n"
        "| Date | Tool | Symptom | Workaround | Status |\n"
        "|---|---|---|---|---|\n"
        "| 2026-11-01 | a | broke | retry | Open - none fixed; waiting |\n"
        "| 2026-11-02 | b | broke | retry | Fixed in v2 |\n"
        "| 2026-11-03 | c | broke | retry |  Open - none fixed |\n"
        "Prose mentioning Open - none fixed is not a row.\n",
        encoding="utf-8",
    )


def full_fixture(si: Env) -> None:
    seed_messages()
    seed_denies()
    write_reliability(si)
    si.write_backlog(2, 5, 3)
    si.state.mkdir(parents=True, exist_ok=True)
    (si.state / "drift-push-count").write_text("4\n")
    si.interactive_review(10)
    si.review_line(
        f"{iso_days_ago(2)} mode=auto status=ran sessions=3 proposals=1 cost_usd=0.12 duration_s=40"
    )


def collect(si: Env, *, escalate: bool = True) -> str:
    return signals.run(escalate_on_fail=escalate, notify=si.notify).summary_line()


def test_writes_every_count_to_the_snapshot(si: Env) -> None:
    full_fixture(si)
    collect(si)
    expected = {
        "INBOX_COUNT": "2", "INBOX_OLDEST_DAYS": "5", "BACKLOG_OPEN": "3",
        "MSGS_UNCLAIMED_14D": "2", "DENIES_7D": "3", "DRIFT_PUSH": "4", "RELIABILITY_OPEN": "2",
        "SIGNAL_TOTAL": "16", "DAYS_SINCE_REVIEW": "10", "LOOP_HEALTH": "PASS",
        "AUTO_REVIEW_HEALTH": "PASS",
    }
    for key, value in expected.items():
        assert si.snapshot_value(key) == value, key
    dt.datetime.strptime(si.snapshot_value("COLLECTED_AT"), "%Y-%m-%dT%H:%M:%SZ")


def test_snapshot_keys_are_in_the_shared_format_order(si: Env) -> None:
    full_fixture(si)
    collect(si)
    keys = [line.split("=", 1)[0] for line in (si.state / "signals.env").read_text().splitlines()]
    assert keys == [
        "COLLECTED_AT", "INBOX_COUNT", "INBOX_OLDEST_DAYS", "BACKLOG_OPEN", "MSGS_UNCLAIMED_14D",
        "DENIES_7D", "DRIFT_PUSH", "RELIABILITY_OPEN", "SIGNAL_TOTAL", "DAYS_SINCE_REVIEW",
        "LOOP_HEALTH", "AUTO_REVIEW_HEALTH",
    ]


def test_prints_one_summary_line_with_both_health_results(si: Env) -> None:
    full_fixture(si)
    line = collect(si)
    assert "\n" not in line
    assert line == (
        "self-improvement: 16 signals (inbox 2/5d, entries 3, msgs 2, denies 3, drift 4, "
        "reliability 2), last review 10d, health PASS, auto-review PASS"
    )


def test_records_unavailable_when_stores_and_drift_count_are_missing(si: Env) -> None:
    si.write_backlog(1, 2, 0)
    si.interactive_review(1, mode="B")
    collect(si)
    assert si.snapshot_value("MSGS_UNCLAIMED_14D") == "unavailable"
    assert si.snapshot_value("DENIES_7D") == "unavailable"
    assert si.snapshot_value("DRIFT_PUSH") == "unavailable"
    assert si.snapshot_value("INBOX_COUNT") == "1"
    assert si.snapshot_value("RELIABILITY_OPEN") == "0"
    assert si.snapshot_value("SIGNAL_TOTAL") == "1"
    assert list(si.stores.iterdir()) == []


def test_an_unreadable_store_is_unavailable(si: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_messages()
    seed_denies()

    def broken(**_: object) -> list[object]:
        raise sqlite3.OperationalError("database is locked")

    from cc_session_tools.lib.messaging import service
    import hooks.telemetry_query

    monkeypatch.setattr(service, "list_messages", broken)
    monkeypatch.setattr(hooks.telemetry_query, "query_events", broken)
    si.write_backlog(0)
    collect(si)
    assert si.snapshot_value("MSGS_UNCLAIMED_14D") == "unavailable"
    assert si.snapshot_value("DENIES_7D") == "unavailable"


def test_records_unavailable_for_backlog_counts_when_backlog_missing(si: Env) -> None:
    si.interactive_review(1, mode="B")
    collect(si)
    assert si.snapshot_value("INBOX_COUNT") == "unavailable"
    assert si.snapshot_value("BACKLOG_OPEN") == "unavailable"
    assert si.snapshot_value("LOOP_HEALTH") == "FAIL"


def test_a_malformed_drift_count_is_unavailable(si: Env) -> None:
    si.write_backlog(0)
    si.state.mkdir(parents=True)
    (si.state / "drift-push-count").write_text("lots\n")
    collect(si)
    assert si.snapshot_value("DRIFT_PUSH") == "unavailable"


@pytest.mark.parametrize(("review_age", "health"), [(21, "PASS"), (22, "FAIL")])
def test_loop_health_review_age_boundary(si: Env, review_age: int, health: str) -> None:
    si.write_backlog(0)
    si.interactive_review(review_age)
    collect(si)
    assert si.snapshot_value("LOOP_HEALTH") == health


@pytest.mark.parametrize(("inbox_age", "health"), [(30, "PASS"), (31, "FAIL")])
def test_loop_health_inbox_age_boundary(si: Env, inbox_age: int, health: str) -> None:
    si.write_backlog(1, inbox_age)
    si.interactive_review(1, mode="B")
    collect(si)
    assert si.snapshot_value("LOOP_HEALTH") == health


def test_loop_health_fail_and_none_when_no_review_recorded(si: Env) -> None:
    si.write_backlog(0)
    line = collect(si)
    assert si.snapshot_value("DAYS_SINCE_REVIEW") == "none"
    assert si.snapshot_value("LOOP_HEALTH") == "FAIL"
    assert "last review none" in line


def test_auto_lines_do_not_reset_days_since_review(si: Env) -> None:
    si.write_backlog(0)
    si.review_line(f"{iso_days_ago(20)} mode=C findings=2 applied=1 deferred=1")
    si.review_line(
        f"{iso_days_ago(1)} mode=auto status=ran sessions=4 proposals=2 cost_usd=0.10 duration_s=30"
    )
    collect(si)
    assert si.snapshot_value("DAYS_SINCE_REVIEW") == "20"


@pytest.mark.parametrize(
    ("line", "health"),
    [
        (f"{iso_days_ago(3)} mode=auto status=skipped sessions=0 proposals=0 cost_usd=0 duration_s=1", "PASS"),
        (f"{iso_days_ago(8)} mode=auto status=ran sessions=4 proposals=2 cost_usd=0.10 duration_s=30", "PASS"),
        (f"{iso_days_ago(9)} mode=auto status=ran sessions=4 proposals=2 cost_usd=0.10 duration_s=30", "FAIL"),
        (f"{iso_days_ago(1)} mode=auto status=failed sessions=4 proposals=0 cost_usd=0 duration_s=30", "FAIL"),
        (f"{iso_days_ago(1)} mode=C findings=0 applied=0 deferred=0", "FAIL"),
    ],
)
def test_auto_review_health(si: Env, line: str, health: str) -> None:
    si.write_backlog(0)
    si.review_line(line)
    collect(si)
    assert si.snapshot_value("AUTO_REVIEW_HEALTH") == health


def test_a_sensitive_subject_never_appears_in_state_or_output(si: Env) -> None:
    full_fixture(si)
    line = collect(si)
    assert "Sensitive-Doe" not in line
    for path in si.state.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "Sensitive-Doe" not in text and "Injunction" not in text


def test_escalates_once_on_loop_health_fail_and_not_again_within_7_days(si: Env) -> None:
    si.write_backlog(0)
    si.interactive_review(40)
    si.review_line(
        f"{iso_days_ago(1)} mode=auto status=ran sessions=1 proposals=0 cost_usd=0.10 duration_s=30"
    )
    collect(si)
    assert si.notify.messages == [
        "Self-improvement: loop health FAIL - last review 40d, 0 signals. "
        "Run /review-session-and-improve-claude-setup periodic"
    ]
    assert (si.state / "last-escalation").read_text() == f"{days_ago(0)}\n"
    collect(si)
    assert len(si.notify.messages) == 1


def test_escalates_on_auto_review_fail_alone(si: Env) -> None:
    si.write_backlog(0)
    si.interactive_review(2)
    collect(si)
    assert si.snapshot_value("LOOP_HEALTH") == "PASS"
    assert len(si.notify.messages) == 1
    assert "auto-review health FAIL" in si.notify.messages[0]
    assert "loop health" not in si.notify.messages[0]


@pytest.mark.parametrize(("age", "sent"), [(7, 1), (6, 0)])
def test_escalation_interval(si: Env, age: int, sent: int) -> None:
    si.write_backlog(0)
    si.state.mkdir(parents=True)
    (si.state / "last-escalation").write_text(f"{days_ago(age)}\n")
    collect(si)
    assert len(si.notify.messages) == sent


def test_does_not_escalate_when_both_checks_pass(si: Env) -> None:
    si.write_backlog(0)
    si.interactive_review(2)
    si.review_line(
        f"{iso_days_ago(1)} mode=auto status=skipped sessions=0 proposals=0 cost_usd=0 duration_s=1"
    )
    collect(si)
    assert si.notify.messages == []


def test_a_failing_escalation_still_writes_the_snapshot_and_no_stamp(si: Env) -> None:
    si.write_backlog(0)
    si.notify.fail = True
    collect(si)
    assert (si.state / "signals.env").is_file()
    assert len(si.notify.messages) == 1
    assert not (si.state / "last-escalation").exists()


def test_no_escalate_env_suppresses_escalation(si: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    si.write_backlog(0)
    monkeypatch.setenv("SELF_IMPROVEMENT_NO_ESCALATE", "1")
    collect(si)
    assert si.snapshot_value("LOOP_HEALTH") == "FAIL"
    assert si.notify.messages == []
    assert not (si.state / "last-escalation").exists()


def test_no_escalate_flag_suppresses_escalation(si: Env) -> None:
    si.write_backlog(0)
    collect(si, escalate=False)
    assert si.notify.messages == []


def test_writes_only_inside_the_state_directory(si: Env) -> None:
    full_fixture(si)
    exclude = [si.state, si.stores]
    before = tree_checksums(si.root, exclude)
    collect(si)
    assert tree_checksums(si.root, exclude) == before
    assert list((si.root / "tmp").iterdir()) == []


def test_an_inbox_line_with_an_impossible_date_is_not_counted(si: Env) -> None:
    si.write_backlog(1, 3)
    text = si.backlog.read_text().replace(
        "## Inbox\n", "## Inbox\n- [2026-02-31] [tag-x] impossible date\n"
    )
    si.backlog.write_text(text)
    collect(si)
    assert si.snapshot_value("INBOX_COUNT") == "1"


def test_inbox_oldest_is_none_for_an_empty_inbox(si: Env) -> None:
    si.write_backlog(0)
    line = collect(si)
    assert si.snapshot_value("INBOX_OLDEST_DAYS") == "none"
    assert "(inbox 0, " in line


def test_reliability_follows_symlinked_skill_directories(si: Env) -> None:
    real = si.root / "elsewhere" / "linked"
    (real / "references").mkdir(parents=True)
    (real / "references" / "log.md").write_text("| x | Open - none fixed |\n")
    skills = si.home / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "linked").symlink_to(real)
    si.write_backlog(0)
    collect(si)
    assert si.snapshot_value("RELIABILITY_OPEN") == "1"
