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


def test_write_log_write_survives_a_broken_log_stream(tmp_path):
    """The whole point of this module is to survive the exact class of transient I/O error
    (a flaky network/DrvFS-backed project root) that the log file itself also lives under. If
    a write to the log stream could raise, a log-file blip during --write's import loop would
    be caught by that loop's own `except OSError` and misreported as a source-file import
    failure, or escape write() as a raw exception for calls outside that loop. The real
    stream must still receive every write even when the log side is broken."""
    project_root = tmp_path / "demo"
    project_root.mkdir()

    class _BrokenLog:
        def write(self, data):
            raise OSError(5, "Input/output error")

        def flush(self):
            raise OSError(5, "Input/output error")

    real_stdout = sys.stdout
    tee = write_log._Tee(real_stdout, _BrokenLog())

    written = tee.write("hello despite a broken log")  # must not raise
    tee.flush()  # must not raise

    assert written == len("hello despite a broken log")


def test_write_log_exit_survives_a_broken_log_stream_while_reporting_an_exception(tmp_path):
    """Same contract as above, for __exit__'s traceback-capture path and its file close."""
    project_root = tmp_path / "demo"
    project_root.mkdir()
    real_stdout, real_stderr = sys.stdout, sys.stderr

    class _BrokenLog:
        def write(self, data):
            raise OSError(5, "Input/output error")

        def flush(self):
            raise OSError(5, "Input/output error")

        def close(self):
            raise OSError(5, "Input/output error")

    log = write_log.WriteLog(project_root)
    log.__enter__()
    log._file = _BrokenLog()  # simulate the log file becoming unwritable mid-run

    try:
        raise ValueError("boom")
    except ValueError:
        exc_type, exc, tb = sys.exc_info()
        log.__exit__(exc_type, exc, tb)  # must not raise despite the broken file

    assert sys.stdout is real_stdout  # streams were still restored
    assert sys.stderr is real_stderr


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
