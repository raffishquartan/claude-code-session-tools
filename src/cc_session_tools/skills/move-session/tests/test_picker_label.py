"""Tests for the picker-label append and the liveness check that guards it."""
from __future__ import annotations

import calendar
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

    def test_invalid_utf8_early_in_transcript_still_appends(self, ms, tmp_path):
        """Only the appended tail is decoded, so a bad byte anywhere earlier
        in the history does not turn a successful append into a traceback."""
        p = tmp_path / f"{UUID}.jsonl"
        p.write_bytes(b'{"type":"user","text":"\xff\xfe"}\n{"type":"user","uuid":"u1"}\n')
        ms.append_picker_label(p, UUID, TAG)
        tail = p.read_bytes().splitlines()[-2:]
        assert [json.loads(l) for l in tail] == ms.picker_label_records(UUID, TAG)

    def test_final_line_longer_than_tail_window_still_verifies(self, ms, tmp_path):
        """The tail window starts inside a final line longer than the window
        (here also holding an invalid byte and multi-byte characters); only
        the two appended lines are decoded."""
        p = tmp_path / f"{UUID}.jsonl"
        long_text = ("\u00e9" * 40_000).encode() + b"\xff"
        p.write_bytes(b'{"type":"user","text":"' + long_text + b'"}')
        assert len(p.read_bytes()) > 64 * 1024
        ms.append_picker_label(p, UUID, TAG)
        tail = p.read_bytes().splitlines()[-2:]
        assert [json.loads(l) for l in tail] == ms.picker_label_records(UUID, TAG)

    def test_wrong_records_after_invalid_utf8_history_exit_1(self, ms, tmp_path, monkeypatch):
        p = tmp_path / f"{UUID}.jsonl"
        p.write_bytes(b'{"type":"user","text":"\xff"}\n')
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

    def test_record_that_is_not_a_json_object_is_skipped(self, ms, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        (sessions / f"{os.getpid()}.json").write_text("[]")
        assert ms.detect_live_holder(UUID, sessions) == []

    def test_unreadable_and_non_pid_files_are_skipped(self, ms, tmp_path):
        """Claude Code may be rewriting a record mid-read, and the directory
        also holds non-record files (e.g. `<pid>.<hash>.key`)."""
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        (sessions / f"{os.getpid()}.json").write_text("{not json")
        (sessions / "notapid.json").write_text(json.dumps({"sessionId": UUID}))
        assert ms.detect_live_holder(UUID, sessions) == []


PROC_START_FORMAT = "%a %b %d %H:%M:%S %Y"


def _real_proc_start(pid: int) -> str:
    """The start time of `pid` in the form Claude Code records as
    `procStart`, read independently of the code under test."""
    out = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True, text=True, check=True,
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
    ).stdout.strip()
    assert out, f"ps printed no start time for pid {pid}"
    return out


def _shifted(proc_start: str, seconds: int) -> str:
    epoch = calendar.timegm(time.strptime(proc_start, PROC_START_FORMAT))
    return time.strftime(PROC_START_FORMAT, time.gmtime(epoch + seconds))


@pytest.fixture
def non_utc_zone(monkeypatch: pytest.MonkeyPatch):
    """Run the code under test (and any `ps` it starts) in a +05:30 zone with
    a British-English (day-before-month) locale, so a comparison that depends on the local zone or
    locale gives the wrong answer."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class TestDetectLiveHolderPidReuse:
    def _record(self, sessions_dir: Path, pid: int, **fields: object) -> Path:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        record = sessions_dir / f"{pid}.json"
        record.write_text(json.dumps({"pid": pid, "sessionId": UUID, **fields}))
        return record

    def _assert_refuses_with_hint(self, reasons: list[str], record: Path) -> None:
        assert len(reasons) == 1
        assert str(record) in reasons[0]
        assert "if no Claude Code session is open" in reasons[0]
        assert "stale" in reasons[0] and "deleted" in reasons[0]

    def test_record_older_than_live_process_is_ignored(self, ms, tmp_path):
        """The live process started well after the record's procStart: the pid
        has been reused by an unrelated process, so the record is stale."""
        pid = os.getpid()
        self._record(tmp_path, pid, procStart=_shifted(_real_proc_start(pid), -60))
        assert ms.detect_live_holder(UUID, tmp_path) == []

    def test_record_with_real_start_refuses(self, ms, tmp_path):
        pid = os.getpid()
        record = self._record(tmp_path, pid, procStart=_real_proc_start(pid))
        self._assert_refuses_with_hint(ms.detect_live_holder(UUID, tmp_path), record)

    def test_record_with_real_start_refuses_in_non_utc_zone(
            self, ms, tmp_path, non_utc_zone):
        pid = os.getpid()
        record = self._record(tmp_path, pid, procStart=_real_proc_start(pid))
        self._assert_refuses_with_hint(ms.detect_live_holder(UUID, tmp_path), record)

    def test_live_process_within_tolerance_refuses(self, ms, tmp_path):
        pid = os.getpid()
        record = self._record(tmp_path, pid, procStart=_shifted(_real_proc_start(pid), -2))
        self._assert_refuses_with_hint(ms.detect_live_holder(UUID, tmp_path), record)

    def test_live_process_started_before_record_refuses(self, ms, tmp_path):
        pid = os.getpid()
        record = self._record(tmp_path, pid, procStart=_shifted(_real_proc_start(pid), 60))
        self._assert_refuses_with_hint(ms.detect_live_holder(UUID, tmp_path), record)

    def test_record_without_proc_start_refuses(self, ms, tmp_path):
        record = self._record(tmp_path, os.getpid())
        self._assert_refuses_with_hint(ms.detect_live_holder(UUID, tmp_path), record)

    @pytest.mark.parametrize("value", ["not a date", "", 1794475503, None])
    def test_record_with_unusable_proc_start_refuses(self, ms, tmp_path, value):
        record = self._record(tmp_path, os.getpid(), procStart=value)
        self._assert_refuses_with_hint(ms.detect_live_holder(UUID, tmp_path), record)

    def test_unreadable_live_start_refuses(self, ms, tmp_path, monkeypatch):
        """A record that would be stale if the live start were known still
        refuses when the live start cannot be read."""
        pid = os.getpid()
        record = self._record(tmp_path, pid, procStart=_shifted(_real_proc_start(pid), -60))
        monkeypatch.setattr(ms, "process_start_time", lambda pid: None)
        self._assert_refuses_with_hint(ms.detect_live_holder(UUID, tmp_path), record)


class TestProcessStartTime:
    def test_matches_ps_in_utc_whatever_the_local_zone(self, ms, non_utc_zone):
        pid = os.getpid()
        expected = calendar.timegm(time.strptime(_real_proc_start(pid), PROC_START_FORMAT))
        assert ms.process_start_time(pid) == expected

    def test_dead_pid_is_none(self, ms):
        assert ms.process_start_time(_dead_pid()) is None

    def _patch_ps(self, ms, monkeypatch, *, raises=None, returncode=0, stdout=""):
        def fake_run(*args, **kwargs):
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(args[0], returncode, stdout, "")
        monkeypatch.setattr(ms.subprocess, "run", fake_run)

    def test_ps_missing_is_none(self, ms, monkeypatch):
        self._patch_ps(ms, monkeypatch, raises=FileNotFoundError("ps"))
        assert ms.process_start_time(os.getpid()) is None

    def test_ps_timeout_is_none(self, ms, monkeypatch):
        self._patch_ps(ms, monkeypatch, raises=subprocess.TimeoutExpired("ps", 1))
        assert ms.process_start_time(os.getpid()) is None

    def test_ps_unparseable_output_is_none(self, ms, monkeypatch):
        self._patch_ps(ms, monkeypatch, stdout="lstart: keyword not found\n")
        assert ms.process_start_time(os.getpid()) is None

    def test_ps_failure_is_none(self, ms, monkeypatch):
        self._patch_ps(ms, monkeypatch, returncode=1, stdout="Thu Nov 12 09:24:55 2026\n")
        assert ms.process_start_time(os.getpid()) is None

    def test_single_digit_day_parses(self, ms, monkeypatch):
        self._patch_ps(ms, monkeypatch, stdout="Wed Nov  4 11:42:34 2026\n")
        assert ms.process_start_time(os.getpid()) == calendar.timegm(
            (2026, 11, 4, 11, 42, 34, 0, 0, 0))
