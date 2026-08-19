"""Tee stdout/stderr to a per-run log file during `ccst pdata init --write`.

--write can run for a long time with zero visible progress otherwise, and a failure can
crash before any of it is reported anywhere durable. WriteLog gives every --write invocation
a flushed-per-line, truncate-per-run log file inside the project, capturing everything the
CLI prints plus the traceback of anything that escapes unhandled.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from types import TracebackType
from typing import TextIO

LOG_FILENAME = "ccst-pdata-init-write.log"


class _Tee:
    """File-like object writing to two streams, flushing the log stream after every write so
    the file is never left holding a buffered-but-unflushed tail if the process dies (crash,
    OOM-kill, Ctrl-C) mid-run."""

    def __init__(self, real_stream: TextIO, log_file: TextIO) -> None:
        self._real = real_stream
        self._log = log_file

    def write(self, data: str) -> int:
        self._real.write(data)
        self._log.write(data)
        self._log.flush()
        return len(data)

    def flush(self) -> None:
        self._real.flush()
        self._log.flush()

    def isatty(self) -> bool:
        return False


class WriteLog:
    """Truncates project_root/ccst-pdata-init-write.log at the start of every --write run —
    one log per invocation, matching the tool's big-bang/one-shot semantics; stitching
    together unrelated attempts into one file would be confusing, not helpful — and tees
    every sys.stdout/sys.stderr write into it for the duration of the `with` block. Any
    exception that escapes the block has its full traceback written to the log before the
    real streams are restored, so it's captured even though Python's own default unhandled-
    exception printer runs after __exit__."""

    def __init__(self, project_root: Path) -> None:
        self._path = project_root / LOG_FILENAME
        self._file: TextIO | None = None
        self._real_stdout: TextIO | None = None
        self._real_stderr: TextIO | None = None

    def __enter__(self) -> WriteLog:
        self._file = open(self._path, "w", buffering=1, encoding="utf-8")
        self._real_stdout, self._real_stderr = sys.stdout, sys.stderr
        sys.stdout = _Tee(self._real_stdout, self._file)
        sys.stderr = _Tee(self._real_stderr, self._file)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # __exit__ only ever runs after a completed __enter__, so these are never still None —
        # the assert makes that contract visible to mypy instead of silently re-checking it.
        assert self._file is not None, "WriteLog.__exit__ called without a completed __enter__"
        assert self._real_stdout is not None, "WriteLog.__exit__ called without a completed __enter__"
        assert self._real_stderr is not None, "WriteLog.__exit__ called without a completed __enter__"

        if exc is not None:
            traceback.print_exception(exc_type, exc, tb, file=self._file)
            self._file.flush()
        sys.stdout, sys.stderr = self._real_stdout, self._real_stderr
        self._file.close()
