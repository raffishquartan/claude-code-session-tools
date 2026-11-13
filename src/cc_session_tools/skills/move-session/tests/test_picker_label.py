"""Tests for the picker-label append and the liveness check that guards it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

UUID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
TAG = "20260503-new-tag"


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _idle_transcript(tmp_path: Path, text: str = '{"type":"user","uuid":"u1"}\n') -> Path:
    p = tmp_path / f"{UUID}.jsonl"
    p.write_text(text)
    idle = time.time() - 600
    os.utime(p, (idle, idle))
    return p


class TestPickerLabelRecords:
    def test_shape_matches_claude_code_rename(self, ms):
        assert ms.picker_label_records(UUID, TAG) == [
            {"type": "custom-title", "customTitle": TAG, "sessionId": UUID},
            {"type": "agent-name", "agentName": TAG, "sessionId": UUID},
        ]


class TestAppendPickerLabel:
    def test_appends_pair_after_existing_bytes(self, ms, tmp_path):
        p = _idle_transcript(tmp_path)
        before = p.read_bytes()
        ms.append_picker_label(p, UUID, TAG)
        after = p.read_bytes()
        assert after.startswith(before)
        assert [json.loads(l) for l in after[len(before):].decode().splitlines()] == (
            ms.picker_label_records(UUID, TAG))

    def test_newline_guard_when_last_byte_is_not_newline(self, ms, tmp_path):
        p = _idle_transcript(tmp_path, '{"type":"user","uuid":"u1"}')
        ms.append_picker_label(p, UUID, TAG)
        lines = p.read_text().splitlines()
        assert len(lines) == 3
        assert [json.loads(l) for l in lines][1:] == ms.picker_label_records(UUID, TAG)

    def test_unparseable_append_exits_1_naming_transcript(self, ms, tmp_path, monkeypatch):
        """If the re-read tail does not parse back to the intended records
        (a truncated or interleaved write), the script exits 1 naming the
        transcript rather than reporting success."""
        p = _idle_transcript(tmp_path)
        monkeypatch.setattr(ms, "encode_record", lambda rec: json.dumps(rec)[:-3])
        with pytest.raises(SystemExit) as exc:
            ms.append_picker_label(p, UUID, TAG)
        # A string SystemExit code exits the process with status 1.
        assert isinstance(exc.value.code, str)
        assert str(p) in exc.value.code

    def test_wrong_records_in_tail_exit_1(self, ms, tmp_path, monkeypatch):
        p = _idle_transcript(tmp_path)
        monkeypatch.setattr(
            ms, "encode_record",
            lambda rec: json.dumps({**rec, "sessionId": "someone-else"},
                                   separators=(",", ":")))
        with pytest.raises(SystemExit) as exc:
            ms.append_picker_label(p, UUID, TAG)
        assert isinstance(exc.value.code, str)
        assert str(p) in exc.value.code


class TestDetectLiveHolder:
    def _record(self, sessions_dir: Path, pid: int, session_uuid: str) -> None:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / f"{pid}.json").write_text(
            json.dumps({"pid": pid, "sessionId": session_uuid}))

    def test_no_records_directory(self, ms, tmp_path):
        assert ms.detect_live_holder(UUID, tmp_path / "sessions") == []

    def test_live_pid_naming_session_refuses(self, ms, tmp_path):
        sessions = tmp_path / "sessions"
        self._record(sessions, os.getpid(), UUID)
        reasons = ms.detect_live_holder(UUID, sessions)
        assert len(reasons) == 1
        assert f"pid {os.getpid()}" in reasons[0]

    def test_dead_pid_is_ignored(self, ms, tmp_path):
        sessions = tmp_path / "sessions"
        self._record(sessions, _dead_pid(), UUID)
        assert ms.detect_live_holder(UUID, sessions) == []

    def test_live_pid_for_other_session_is_ignored(self, ms, tmp_path):
        sessions = tmp_path / "sessions"
        self._record(sessions, os.getpid(), "another-session")
        assert ms.detect_live_holder(UUID, sessions) == []

    def test_unreadable_and_non_pid_files_are_skipped(self, ms, tmp_path):
        """Claude Code may be rewriting a record mid-read, and the directory
        also holds non-record files (e.g. `<pid>.<hash>.key`)."""
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        (sessions / f"{os.getpid()}.json").write_text("{not json")
        (sessions / "notapid.json").write_text(json.dumps({"sessionId": UUID}))
        assert ms.detect_live_holder(UUID, sessions) == []
