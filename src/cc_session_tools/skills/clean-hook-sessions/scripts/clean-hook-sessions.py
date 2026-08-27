#!/usr/bin/env python3
"""Archive and delete Claude Code hook-security-check session transcripts.

See ../SKILL.md for full documentation.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

HOOK_MARKER = "Review this shell command for security risks"
DEFAULT_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
DEFAULT_ARCHIVE_DIR = os.path.expanduser("~/.claude-projects-archive")


def first_user_text(path):
    """Return the text content of the first type=user entry in the jsonl, or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 50:
                    return None
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "user":
                    continue
                msg = d.get("message", {}) or {}
                content = msg.get("content", "")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            return c.get("text", "") or ""
                    return ""
                if isinstance(content, str):
                    return content
                return ""
    except OSError:
        return None
    return None


def find_hook_files(projects_dir):
    """Return list of (mtime, path, size) for every hook-security-check jsonl."""
    pattern = os.path.join(projects_dir, "**", "*.jsonl")
    results = []
    for path in glob.glob(pattern, recursive=True):
        text = first_user_text(path)
        if text is None:
            continue
        if HOOK_MARKER not in text:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        results.append((st.st_mtime, path, st.st_size))
    return results


def partition(files, older_than_days, keep_n):
    """Split files into (to_delete, to_keep, reasons_kept).

    files: list of (mtime, path, size) sorted newest-first.
    - Always keep the newest keep_n regardless of age.
    - Of the remainder, delete those older than the cutoff.
    - Keep the rest (newer than cutoff but outside keep-n).
    """
    files_sorted = sorted(files, reverse=True)
    cutoff = datetime.now().timestamp() - older_than_days * 86400

    to_delete = []
    to_keep = []
    for i, entry in enumerate(files_sorted):
        mt, _, _ = entry
        if i < keep_n:
            to_keep.append((entry, "within keep-n"))
            continue
        if mt >= cutoff:
            to_keep.append((entry, "newer than cutoff"))
        else:
            to_delete.append(entry)
    return to_delete, to_keep


def human_size(n):
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}"
        f /= 1024


def human_time(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def make_archive(files, archive_path):
    """Create tar.gz of the given files. Returns number of files archived, or raises."""
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as tf:
        list_path = tf.name
        for _, path, _ in files:
            tf.write(path + "\n")
    try:
        result = subprocess.run(
            ["tar", "-czf", archive_path, "-T", list_path, "-P"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"tar failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        verify = subprocess.run(
            ["tar", "-tzf", archive_path],
            capture_output=True,
            text=True,
        )
        if verify.returncode != 0:
            raise RuntimeError(
                f"tar verify failed (rc={verify.returncode}): {verify.stderr.strip()}"
            )
        archived_count = sum(1 for _ in verify.stdout.splitlines())
        return archived_count
    finally:
        os.unlink(list_path)


def main():
    parser = argparse.ArgumentParser(
        description="Archive and delete Claude Code hook-security-check session transcripts."
    )
    parser.add_argument(
        "--older-than",
        type=float,
        default=7.0,
        help="Only consider files older than this many days (default: 7).",
    )
    parser.add_argument(
        "--keep-n",
        type=int,
        default=10,
        help="Always keep the N newest hook sessions regardless of age (default: 10).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create the archive and delete files. Without this, dry-run only.",
    )
    parser.add_argument(
        "--archive-dir",
        default=DEFAULT_ARCHIVE_DIR,
        help=f"Directory for tar.gz backup (default: {DEFAULT_ARCHIVE_DIR}).",
    )
    parser.add_argument(
        "--projects-dir",
        default=DEFAULT_PROJECTS_DIR,
        help=f"Root dir to scan (default: {DEFAULT_PROJECTS_DIR}).",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.projects_dir):
        print(f"ERROR: projects dir does not exist: {args.projects_dir}", file=sys.stderr)
        return 2

    print(f"Scanning {args.projects_dir} ...")
    hook_files = find_hook_files(args.projects_dir)
    print(f"Found {len(hook_files)} hook-security-check sessions.")
    if not hook_files:
        print("Nothing to do.")
        return 0

    to_delete, to_keep = partition(hook_files, args.older_than, args.keep_n)

    print()
    print(f"Keep: {len(to_keep)} files")
    print(f"  (policy: keep newest {args.keep_n}; keep files newer than {args.older_than} days)")
    print(f"Delete: {len(to_delete)} files, {human_size(sum(s for _,_,s in to_delete))}")
    if to_delete:
        oldest = min(mt for mt, _, _ in to_delete)
        newest = max(mt for mt, _, _ in to_delete)
        print(f"  oldest: {human_time(oldest)}")
        print(f"  newest: {human_time(newest)}")

    if not to_delete:
        print("Nothing to delete.")
        return 0

    if not args.execute:
        print()
        print("DRY RUN - no files touched. Rerun with --execute to proceed.")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = os.path.join(args.archive_dir, f"hook-sessions-backup-{ts}.tar.gz")
    print()
    print(f"Creating archive: {archive_path}")
    try:
        archived = make_archive(to_delete, archive_path)
    except Exception as e:
        print(f"ERROR creating archive: {e}", file=sys.stderr)
        return 3

    if archived != len(to_delete):
        print(
            f"ERROR: archive contains {archived} files but {len(to_delete)} expected. Aborting delete.",
            file=sys.stderr,
        )
        return 4

    size = os.path.getsize(archive_path)
    print(f"Archive OK: {archived} files, {human_size(size)}")

    print(f"Deleting {len(to_delete)} files...")
    deleted = 0
    errors = []
    for _, path, _ in to_delete:
        try:
            os.unlink(path)
            deleted += 1
        except OSError as e:
            errors.append(f"{path}: {e}")

    print(f"Deleted {deleted} files.")
    if errors:
        print(f"  {len(errors)} errors:", file=sys.stderr)
        for e in errors[:10]:
            print(f"    {e}", file=sys.stderr)

    print()
    print(f"Backup preserved at: {archive_path}")
    return 0 if not errors else 5


if __name__ == "__main__":
    sys.exit(main())
