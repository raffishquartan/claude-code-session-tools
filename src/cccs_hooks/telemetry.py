"""Hook-fire telemetry: writes structured JSONL to ~/.cache/claude/logs/fires.jsonl.

All bash hooks call this module via:
    echo "$INPUT" | python3 -m cccs_hooks.telemetry log --hook NAME ...

Never raises — write failures are logged to stderr and silently suppressed so
a telemetry error never blocks a hook.

Override the directory with the CCCS_HOOKS_DIR env var (retained for test isolation
and legacy overrides; now points at a logs directory, not a hooks directory).

Rotation: when fires.jsonl exceeds _ROTATION_BYTES (default 10 MB), it is
rotated to fires.jsonl.1; existing .1 shifts to .2, and so on up to
_ROTATION_KEEP slots. Anything older than slot _ROTATION_KEEP is dropped.
Explicit pruning: use ``ccst telemetry trim`` (cccs_hooks.telemetry_trim).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import fcntl
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Literal

_DEFAULT_HOOKS_DIR = Path.home() / ".cache" / "claude" / "logs"
_ROTATION_BYTES = 10 * 1024 * 1024  # 10 MB
_ROTATION_KEEP = 3


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


def maybe_rotate(fires: Path, max_size_mb: float = 10.0, keep: int = _ROTATION_KEEP) -> bool:
    """Rotate fires.jsonl if it exceeds max_size_mb.

    Shifts fires.jsonl → fires.jsonl.1, fires.jsonl.1 → fires.jsonl.2, …
    Drops anything older than slot ``keep``.
    Returns True if rotation occurred, False otherwise.
    """
    if not fires.exists() or fires.stat().st_size <= max_size_mb * 1024 * 1024:
        return False
    # Drop oldest slot
    oldest = fires.parent / f"{fires.name}.{keep}"
    if oldest.exists():
        oldest.unlink()
    # Shift slots down
    for i in range(keep - 1, 0, -1):
        src = fires.parent / f"{fires.name}.{i}"
        dst = fires.parent / f"{fires.name}.{i + 1}"
        if src.exists():
            src.rename(dst)
    # Rotate current → .1
    rotated = fires.parent / f"{fires.name}.1"
    shutil.copy2(str(fires), str(rotated))
    fires.unlink()
    return True


def _rotate_if_needed(fires: Path) -> None:
    """Thin wrapper used by log_event — delegates to maybe_rotate."""
    maybe_rotate(fires)


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
