"""Hook-fire telemetry: writes structured JSONL to ~/.claude/hooks/fires.jsonl.

All bash hooks call this module via:
    echo "$INPUT" | python3 -m cccs_hooks.telemetry log --hook NAME ...

Never raises — write failures are logged to stderr and silently suppressed so
a telemetry error never blocks a hook.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import fcntl
import gzip
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

_DEFAULT_HOOKS_DIR = Path.home() / ".claude" / "hooks"
_ROTATION_BYTES = 512 * 1024  # 512 KB


@dataclasses.dataclass(frozen=True, slots=True)
class TelemetryEntry:
    hook: str
    event: str
    tool: str
    session_id: str
    cwd_short: str
    decision: Literal["allow", "deny", "annotate"]
    cache: Literal["hit", "miss", "none"]
    verdict: str
    input_hash: str

    def to_json_line(self) -> str:
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            json.dumps(
                {
                    "v": 1,
                    "ts": ts,
                    "hook": self.hook,
                    "event": self.event,
                    "tool": self.tool,
                    "session_id": self.session_id,
                    "cwd": self.cwd_short,
                    "decision": self.decision,
                    "cache": self.cache,
                    "verdict": self.verdict,
                    "input_hash": self.input_hash,
                },
                separators=(",", ":"),
            )
            + "\n"
        )


def _rotate_if_needed(fires: Path) -> None:
    if not fires.exists() or fires.stat().st_size <= _ROTATION_BYTES:
        return
    week = time.strftime("%Y-W%W")
    rotated = fires.parent / f"fires.{week}.jsonl.gz"
    counter = 0
    while rotated.exists():
        counter += 1
        rotated = fires.parent / f"fires.{week}.{counter}.jsonl.gz"
    with fires.open("rb") as f_in, gzip.open(rotated, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    fires.unlink()


def log_event(entry: TelemetryEntry, *, hooks_dir: Path | None = None) -> None:
    """Append entry to fires.jsonl. Never raises."""
    target_dir = hooks_dir if hooks_dir is not None else _DEFAULT_HOOKS_DIR
    fires = target_dir / "fires.jsonl"
    try:
        fires.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _rotate_if_needed(fires)
        line = entry.to_json_line()
        fd = os.open(str(fires), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except OSError as e:
        print(f"[telemetry-warn] log write failed: {e}", file=sys.stderr)


def _shorten_cwd(cwd: str) -> str:
    """Keep last 2 path components to limit PII exposure in the log."""
    parts = Path(cwd).parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else cwd


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cccs_hooks.telemetry")
    sub = p.add_subparsers(dest="cmd")
    log_p = sub.add_parser("log")
    log_p.add_argument("--hook", required=True)
    log_p.add_argument("--event", required=True)
    log_p.add_argument("--decision", required=True)
    log_p.add_argument("--cache", default="none")
    log_p.add_argument("--verdict", default="")
    log_p.add_argument("--input-hash", default="")
    args = p.parse_args(argv)
    if args.cmd != "log":
        p.print_help()
        return 1
    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    hooks_dir_env = os.environ.get("CCCS_HOOKS_DIR")
    hooks_dir = Path(hooks_dir_env) if hooks_dir_env else None
    entry = TelemetryEntry(
        hook=args.hook,
        event=args.event,
        tool=str(data.get("tool_name", "")),
        session_id=str(data.get("session_id", "")),
        cwd_short=_shorten_cwd(str(data.get("cwd", ""))),
        decision=args.decision,
        cache=args.cache,
        verdict=args.verdict,
        input_hash=args.input_hash,
    )
    log_event(entry, hooks_dir=hooks_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
