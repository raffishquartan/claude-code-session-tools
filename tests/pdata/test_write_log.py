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


def test_write_log_flushes_a_write_with_no_trailing_newline(tmp_path):
    """buffering=1 (line-buffering) only auto-flushes on '\\n'/'\\r' — a write with no
    line-ending would sit unflushed in the OS-level buffer under line-buffering alone. The
    explicit .flush() in _Tee.write() must cover this case too, since it's what actually
    backs the "durable even on a hard crash mid-run" guarantee the module's docstring makes."""
    project_root = tmp_path / "demo"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    with write_log.WriteLog(project_root):
        sys.stdout.write("partial, no newline")
        assert log_path.read_text() == "partial, no newline"


def test_write_log_writes_non_ascii_content(tmp_path):
    """The log captures free-form stdout/stderr, including project paths and exception
    messages that may contain non-ASCII characters — must not depend on the platform's
    default locale encoding."""
    project_root = tmp_path / "demo"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    with write_log.WriteLog(project_root):
        print("café — naïve façade 日本語")

    assert "café — naïve façade 日本語" in log_path.read_text(encoding="utf-8")


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
