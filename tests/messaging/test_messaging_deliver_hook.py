# tests/messaging/test_messaging_deliver_hook.py
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cccs_hooks import messaging_deliver
from cc_session_tools.lib.messaging import service


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _capture_emit(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(messaging_deliver, "_emit", lambda ctx, event: out.append(ctx))
    return out


def _capture_failures(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reasons: list[str] = []
    monkeypatch.setattr(messaging_deliver, "_log_failure", lambda reason: reasons.append(reason))
    return reasons


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(messaging_deliver, "_emit", lambda ctx, event: events.append(event))
    return events


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit"])
def test_emits_event_name_matching_invoking_event(
    event: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the echoed hookSpecificOutput.hookEventName must match the
    invoking event, read from the snake_case `hook_event_name` stdin field. The
    old camelCase read defaulted to UserPromptSubmit and broke SessionStart."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _stdin(monkeypatch, {"hook_event_name": event, "session_id": "u", "cwd": str(tmp_path)})
    events = _capture_events(monkeypatch)
    assert messaging_deliver.main() == 0
    assert events == [event]


def test_hook_emits_digest_for_addressed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    # Send to the session uuid we will present as the recipient.
    cwd = tmp_path / "work"
    cwd.mkdir()
    from cc_session_tools.lib.messaging import store
    partition = store.partition_for_cwd(cwd)
    service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="session", to_value="recipient-uuid", to_partition=partition,
        subject="Ping", body="b", attachments=[], thread=None,
    ))
    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "recipient-uuid", "cwd": str(cwd)})
    emitted = _capture_emit(monkeypatch)
    rc = messaging_deliver.main()
    assert rc == 0
    assert any("Ping" in e for e in emitted)


def test_hook_emits_empty_on_bad_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    emitted = _capture_emit(monkeypatch)
    reasons = _capture_failures(monkeypatch)
    rc = messaging_deliver.main()
    assert rc == 0
    assert emitted == [""]  # degrades to empty context, never blocks
    assert reasons == ["bad-stdin"]  # the failure is reported, not swallowed


def test_hook_emits_empty_on_non_dict_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    # Valid JSON but not an object: must degrade, not raise AttributeError.
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
    emitted = _capture_emit(monkeypatch)
    reasons = _capture_failures(monkeypatch)
    rc = messaging_deliver.main()
    assert rc == 0
    assert emitted == [""]
    assert reasons == ["bad-stdin"]


def test_hook_degrades_when_deliver_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise OSError("disk gone")

    monkeypatch.setattr(service, "deliver", _boom)
    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u", "cwd": str(tmp_path)})
    emitted = _capture_emit(monkeypatch)
    reasons = _capture_failures(monkeypatch)
    rc = messaging_deliver.main()
    assert rc == 0
    assert emitted == [""]
    assert reasons == ["OSError"]


def test_hook_does_not_resurface_after_first_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    cwd = tmp_path / "work"
    cwd.mkdir()
    from cc_session_tools.lib.messaging import store
    partition = store.partition_for_cwd(cwd)
    service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="session", to_value="recipient-uuid", to_partition=partition,
        subject="Once", body="b", attachments=[], thread=None,
    ))
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "recipient-uuid", "cwd": str(cwd)}
    _stdin(monkeypatch, payload)
    out1 = _capture_emit(monkeypatch)
    messaging_deliver.main()
    assert any("Once" in e for e in out1)
    _stdin(monkeypatch, payload)
    out2 = _capture_emit(monkeypatch)
    messaging_deliver.main()
    assert not any("Once" in e for e in out2)
