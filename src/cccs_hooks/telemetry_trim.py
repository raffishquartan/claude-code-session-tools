"""CLI for explicit telemetry pruning: ccst telemetry trim.

Trims ~/.claude/hooks/fires.jsonl by:
  --max-size <MB>      Rotate (gzip) any entries when the file exceeds this size.
  --max-age-days <N>   Drop JSONL lines older than N days from fires.jsonl.

Both flags are optional and can be combined. Without any flags, no pruning is
done and the tool prints the current file size.

Designed to be invoked via ``ccst telemetry trim``; can also run directly as
``python -m cccs_hooks.telemetry_trim``.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import json
import shutil
import sys
from pathlib import Path

_DEFAULT_HOOKS_DIR = Path.home() / ".claude" / "hooks"


def _fires_path(hooks_dir: Path | None = None) -> Path:
    return (hooks_dir or _DEFAULT_HOOKS_DIR) / "fires.jsonl"


def trim_by_age(fires: Path, max_age_days: int) -> tuple[int, int]:
    """Remove lines older than max_age_days from fires.

    Returns (kept, removed).
    """
    if not fires.exists():
        return 0, 0

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age_days)
    kept_lines: list[str] = []
    removed = 0

    with fires.open() as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if not raw:
                continue
            try:
                data = json.loads(raw)
                ts_str = data.get("ts", "")
                if ts_str:
                    ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts < cutoff:
                        removed += 1
                        continue
            except (json.JSONDecodeError, ValueError):
                pass  # keep malformed lines — don't silently destroy data
            kept_lines.append(raw)

    fires.write_text("\n".join(kept_lines) + ("\n" if kept_lines else ""))
    return len(kept_lines), removed


def rotate_by_size(fires: Path, max_size_mb: float, keep: int = 3) -> bool:
    """Rotate fires.jsonl if it exceeds max_size_mb.

    Rotates: fires.jsonl → fires.jsonl.1, fires.jsonl.1 → fires.jsonl.2, ...
    Drops anything older than fires.jsonl.<keep>.
    Returns True if rotation occurred.
    """
    if not fires.exists():
        return False
    max_bytes = max_size_mb * 1024 * 1024
    if fires.stat().st_size <= max_bytes:
        return False

    # Drop oldest rotation slot
    oldest = fires.parent / f"{fires.name}.{keep}"
    if oldest.exists():
        oldest.unlink()

    # Shift .N-1 → .N down to .1 → .2
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


def trim(
    *,
    max_size_mb: float | None = None,
    max_age_days: int | None = None,
    hooks_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Run the trim operation. Returns a summary dict."""
    fires = _fires_path(hooks_dir)
    summary: dict[str, object] = {
        "path": str(fires),
        "exists": fires.exists(),
        "size_bytes": fires.stat().st_size if fires.exists() else 0,
        "rotated": False,
        "lines_kept": None,
        "lines_removed": None,
    }

    if not fires.exists():
        return summary

    if dry_run:
        # Just report what would happen
        if max_size_mb is not None:
            summary["would_rotate"] = fires.stat().st_size > max_size_mb * 1024 * 1024
        if max_age_days is not None:
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                days=max_age_days
            )
            would_remove = 0
            with fires.open() as f:
                for raw in f:
                    raw = raw.rstrip("\n")
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                        ts_str = data.get("ts", "")
                        if ts_str:
                            ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts < cutoff:
                                would_remove += 1
                    except (json.JSONDecodeError, ValueError):
                        pass
            summary["would_remove_lines"] = would_remove
        return summary

    # Age trim first (keeps file smaller before rotation check)
    if max_age_days is not None:
        kept, removed = trim_by_age(fires, max_age_days)
        summary["lines_kept"] = kept
        summary["lines_removed"] = removed

    # Then size-based rotation
    if max_size_mb is not None:
        rotated = rotate_by_size(fires, max_size_mb)
        summary["rotated"] = rotated

    # Refresh size after operations
    summary["size_bytes"] = fires.stat().st_size if fires.exists() else 0
    return summary


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``ccst telemetry trim``."""
    p = argparse.ArgumentParser(
        prog="ccst telemetry trim",
        description="Trim ~/.claude/hooks/fires.jsonl by size and/or age.",
    )
    p.add_argument(
        "--max-size",
        type=float,
        metavar="MB",
        help="Rotate fires.jsonl when it exceeds this size in MB (e.g. 5 for 5 MB)",
    )
    p.add_argument(
        "--max-age-days",
        type=int,
        metavar="N",
        help="Drop lines older than N days from fires.jsonl",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done without making changes (default: apply changes)",
    )
    p.add_argument(
        "--hooks-dir",
        default=None,
        metavar="DIR",
        help="Hooks directory (default: ~/.claude/hooks/)",
    )
    args = p.parse_args(argv)

    hooks_dir = Path(args.hooks_dir) if args.hooks_dir else None
    fires = _fires_path(hooks_dir)

    if not fires.exists():
        print(f"No telemetry file found at {fires}")
        return 0

    size_bytes = fires.stat().st_size
    print(f"Telemetry file: {fires}")
    print(f"Current size:   {size_bytes:,} bytes ({size_bytes / 1024:.1f} KB)")

    if args.max_size is None and args.max_age_days is None:
        print("No trim flags specified. Use --max-size and/or --max-age-days.")
        return 0

    result = trim(
        max_size_mb=args.max_size,
        max_age_days=args.max_age_days,
        hooks_dir=hooks_dir,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("Dry run — no changes made.")
        if "would_rotate" in result:
            flag = result["would_rotate"]
            print(f"  Would rotate: {'yes' if flag else 'no (below size threshold)'}")
        if "would_remove_lines" in result:
            n = result["would_remove_lines"]
            print(f"  Would remove: {n} line(s) older than {args.max_age_days} day(s)")
    else:
        if result.get("lines_removed") is not None:
            print(
                f"  Age trim: kept {result['lines_kept']} line(s), "
                f"removed {result['lines_removed']} line(s)"
            )
        if result.get("rotated"):
            print("  Rotated: fires.jsonl → fires.jsonl.1")
        new_size = result.get("size_bytes", 0)
        if fires.exists():
            print(f"  New size: {new_size:,} bytes ({new_size / 1024:.1f} KB)")  # type: ignore[arg-type]
        else:
            print("  File was rotated away — fires.jsonl no longer exists.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
