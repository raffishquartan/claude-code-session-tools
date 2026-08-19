"""Tests for lib/pdata/write_log.py."""
from __future__ import annotations

import sys

from cc_session_tools.lib.pdata import write_log


def test_write_log_tees_stdout_into_the_log_file(tmp_path, capsys):
    project_root = tmp_path / "demo"
    project_root.mkdir()

    with write_log.WriteLog(project_root):
        print("hello from stdout")

    captured = capsys.readouterr()
    assert "hello from stdout" in captured.out

    log_path = project_root / write_log.LOG_FILENAME
    assert "hello from stdout" in log_path.read_text()


def test_write_log_flushes_after_every_write(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    with write_log.WriteLog(project_root):
        print("first line")
        # Read the file from a second, independent handle while the first is still open —
        # only possible if the write above was actually flushed to disk already.
        assert "first line" in log_path.read_text()


def test_write_log_captures_traceback_of_an_exception_that_escapes(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    try:
        with write_log.WriteLog(project_root):
            raise ValueError("boom")
    except ValueError:
        pass

    content = log_path.read_text()
    assert "ValueError" in content
    assert "boom" in content


def test_write_log_restores_real_stdout_and_stderr_on_exit(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    real_stdout, real_stderr = sys.stdout, sys.stderr

    with write_log.WriteLog(project_root):
        assert sys.stdout is not real_stdout

    assert sys.stdout is real_stdout
    assert sys.stderr is real_stderr


def test_write_log_truncates_across_successive_runs(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    with write_log.WriteLog(project_root):
        print("first run, a long line of output")

    with write_log.WriteLog(project_root):
        print("second run")

    content = log_path.read_text()
    assert "second run" in content
    assert "first run" not in content
