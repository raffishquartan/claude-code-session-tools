"""End-to-end test: invoke move_session.py as a subprocess against a synthetic
source and assert all artifacts are correct."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "move_session.py"
# Prepend the repo's src so the subprocess uses the local cc_session_tools,
# not a previously installed editable install that may lag behind.
_REPO_SRC = str(Path(__file__).resolve().parents[3] / "src")


def _run(*args, env=None):
    """Invoke the script and return (returncode, stdout, stderr)."""
    full_env = os.environ.copy()
    # Strip CLAUDECODE so the in-session detector doesn't trip in a real CC
    # context where these tests are being executed.
    full_env.pop("CLAUDECODE", None)
    full_env.pop("CLAUDE_PROJECT_DIR", None)
    # Ensure the local cc_session_tools takes precedence over any installed version.
    existing = full_env.get("PYTHONPATH", "")
    full_env["PYTHONPATH"] = _REPO_SRC + (":" + existing if existing else "")
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=full_env,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestEndToEnd:
    def test_move_and_rename_creates_all_artifacts(
            self, tmp_home, projects_root, roots_file, make_session, tmp_path):
        """Full MOVE+RENAME: dst dir copied, dst jsonl rewritten, tombstone
        appended, marker written, cleanup script generated."""
        src_dir, src_jsonl, uuid = make_session("src-proj", "20260503-source")
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--rename-tag", "20260503-renamed",
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"script failed: {err}\n{out}"
        assert "VERIFICATION" in out
        assert "RESULT              : PASS" in out

        # Destination cc-sessions dir exists with copied content.
        dst_dir = dst_cwd / "cc-sessions" / "20260503-renamed"
        assert dst_dir.is_dir()
        assert (dst_dir / "working" / "WORKLOG.md").read_text() == "test worklog\n"

        # Destination jsonl exists in the new project key, with paths rewritten.
        dst_key = str(dst_cwd).replace("/", "-")
        dst_jsonl = tmp_home / ".claude" / "projects" / dst_key / f"{uuid}.jsonl"
        assert dst_jsonl.is_file()
        text = dst_jsonl.read_text()
        assert str(src_dir.parent.parent) not in text  # old cwd gone
        assert str(dst_cwd) in text  # new cwd present

        # Pending-rename marker written in dst.
        marker = dst_dir / ".pending-rename"
        assert marker.is_file()
        assert "20260503-renamed" in marker.read_text()

        # Tombstone written at source.
        assert (src_dir / "TOMBSTONE.md").is_file()

        # Cleanup script generated and points at /tmp.
        # Find the path it printed.
        cleanup_lines = [l for l in out.splitlines()
                         if "/tmp/move-session-cleanup-" in l]
        assert cleanup_lines, "cleanup script path not printed"

    def test_dry_run_writes_nothing(
            self, tmp_home, projects_root, roots_file, make_session):
        src_dir, _, uuid = make_session("src-proj", "20260503-dry")
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--rename-tag", "20260503-newname",
            "--uuid", uuid,
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0
        assert "DRY-RUN" in out
        assert not (dst_cwd / "cc-sessions").exists()

    def test_refuses_to_clobber_existing_destination(
            self, tmp_home, projects_root, roots_file, make_session):
        src_dir, _, uuid = make_session("src-proj", "20260503-src")
        dst_cwd = projects_root / "dst-proj"
        dst_dir = dst_cwd / "cc-sessions" / "20260503-renamed"
        dst_dir.mkdir(parents=True)  # pre-existing
        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--rename-tag", "20260503-renamed",
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc != 0
        assert "ABORT" in (out + err)

    def test_invalid_dst_root_rejected(
            self, tmp_home, projects_root, roots_file, make_session, tmp_path):
        src_dir, _, uuid = make_session("src-proj", "20260503-src")
        # Destination outside any registered root.
        bogus = tmp_path / "not-a-root" / "proj"
        bogus.mkdir(parents=True)
        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(bogus),
            "--rename-tag", "20260503-renamed",
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc != 0
        assert "not a direct subdirectory" in (out + err)

    def test_tag_validation_failure(
            self, tmp_home, projects_root, roots_file, make_session):
        src_dir, _, uuid = make_session("src-proj", "20260503-src")
        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--rename-tag", "20260504-changed-date-prefix",
            "--uuid", uuid,
            env={"HOME": str(tmp_home)},
        )
        assert rc != 0
        assert "date prefix is immutable" in (out + err)

    def test_auto_includes_memory_when_sole_session(
            self, tmp_home, projects_root, roots_file, make_session):
        """Item 3.5: source key has only one session -> memory auto-copied."""
        src_dir, _, uuid = make_session("src-proj", "20260503-only")
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()
        # Plant a memory dir alongside the source jsonl.
        src_key_dir = (tmp_home / ".claude" / "projects" /
                       str(projects_root / "src-proj").replace("/", "-"))
        (src_key_dir / "memory").mkdir()
        (src_key_dir / "memory" / "MEMORY.md").write_text("- item 1\n")

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--rename-tag", "20260503-newname",
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"
        assert "auto: source key had only this session" in out
        dst_key_dir = (tmp_home / ".claude" / "projects" /
                       str(dst_cwd).replace("/", "-"))
        assert (dst_key_dir / "memory" / "MEMORY.md").is_file()

    def test_does_not_auto_include_memory_when_siblings(
            self, tmp_home, projects_root, roots_file, make_session):
        """If source key has siblings, memory belongs to the project, not the
        moving session - leave it alone."""
        src_dir, _, uuid = make_session(
            "src-proj", "20260503-one", extra_jsonls=2)
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()
        src_key_dir = (tmp_home / ".claude" / "projects" /
                       str(projects_root / "src-proj").replace("/", "-"))
        (src_key_dir / "memory").mkdir()
        (src_key_dir / "memory" / "MEMORY.md").write_text("- shared\n")

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--rename-tag", "20260503-renamed",
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0
        assert "memory NOT copied" in out
        dst_key_dir = (tmp_home / ".claude" / "projects" /
                       str(dst_cwd).replace("/", "-"))
        assert not (dst_key_dir / "memory").exists()

    def test_plan_shows_matched_root(
            self, tmp_home, projects_root, roots_file, make_session):
        """Item 3.7.1: plan output shows which root the dst cwd is under."""
        src_dir, _, uuid = make_session("src-proj", "20260503-x")
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()
        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--rename-tag", "20260503-y",
            "--uuid", uuid,
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0
        assert "dst cwd valid      : yes" in out
        assert str(projects_root) in out


class TestRenameOnlyDoesNotCorruptJsonl:
    """Item 5.2.1: rename-only must NOT append tombstone records to the jsonl,
    because src_jsonl == dst_jsonl when the project key is unchanged.
    Appending would corrupt the live transcript that the user resumes into."""

    def test_rename_only_jsonl_line_count_unchanged(
            self, tmp_home, projects_root, roots_file, make_session):
        src_dir, src_jsonl, uuid = make_session("proj", "20260503-old-tag")
        before_lines = src_jsonl.read_text().count("\n")
        before_text = src_jsonl.read_text()

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--rename-tag", "20260503-new-tag",
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"

        # The script announces the deliberate skip so future readers understand why.
        assert "jsonl tombstone records skipped (rename-only" in out
        # Most importantly: the jsonl is byte-identical to before. Any change
        # would mean tombstone records leaked into the live transcript.
        after_text = src_jsonl.read_text()
        assert after_text == before_text, (
            "rename-only must not modify the jsonl; it is shared with destination"
        )
        after_lines = src_jsonl.read_text().count("\n")
        assert after_lines == before_lines

        # TOMBSTONE.md still goes into the source dir (preserved as a record).
        assert (src_dir / "TOMBSTONE.md").is_file()

    def test_move_does_append_tombstone_records_to_source(
            self, tmp_home, projects_root, roots_file, make_session):
        """Counterpart: confirm that the MOVE path DOES still append records
        to the source jsonl. The fix above must not regress the move path."""
        src_dir, src_jsonl, uuid = make_session("src-proj", "20260503-src")
        before_lines = sum(1 for _ in src_jsonl.open())
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"
        assert "appended 2 tombstone records" in out
        after_lines = sum(1 for _ in src_jsonl.open())
        assert after_lines == before_lines + 2


class TestIncludeMemoryWarnInRenameOnly:
    """Item 5.4.2: --include-memory passed to a rename-only operation should
    emit a WARNING and continue (no copy possible because project key is
    unchanged)."""

    def test_warning_printed_and_no_copy_attempted(
            self, tmp_home, projects_root, roots_file, make_session):
        src_dir, src_jsonl, uuid = make_session("proj", "20260503-old")
        # Plant a memory dir to make a copy attempt visible if it were made.
        src_key_dir = src_jsonl.parent
        (src_key_dir / "memory").mkdir()
        (src_key_dir / "memory" / "MEMORY.md").write_text("- shared\n")

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--rename-tag", "20260503-new",
            "--uuid", uuid,
            "--include-memory",
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"
        assert "WARNING: --include-memory ignored (rename-only" in out
        # Same project key - the memory dir is unchanged where it was, and
        # there is no other key to copy to. Just confirm we didn't somehow
        # create a duplicate.
        all_memory_dirs = list(
            (tmp_home / ".claude" / "projects").glob("*/memory"))
        assert len(all_memory_dirs) == 1


class TestTaskMigration:
    """Task files under ~/.claude/tasks/<src-session-key>/ are migrated to
    ~/.claude/tasks/<dst-session-key>/ on execute. The src-session-key is the
    full abs path of the source session dir with '/' replaced by '-'."""

    def _make_task_files(
        self, tmp_home: Path, session_dir: Path, *, count: int = 3
    ) -> tuple[Path, list[str]]:
        """Create `count` synthetic task JSON files in the task dir for session_dir."""
        import uuid as uuidlib
        task_key = str(session_dir).replace("/", "-")
        task_dir = tmp_home / ".claude" / "tasks" / task_key
        task_dir.mkdir(parents=True)
        ids = []
        for _ in range(count):
            uid = str(uuidlib.uuid4())
            (task_dir / f"{uid}.json").write_text(json.dumps({"id": uid, "title": "test"}))
            ids.append(uid)
        return task_dir, ids

    def _dst_task_dir(self, tmp_home: Path, dst_session_dir: Path) -> Path:
        dst_task_key = str(dst_session_dir).replace("/", "-")
        return tmp_home / ".claude" / "tasks" / dst_task_key

    def test_task_files_migrated_on_move(
            self, tmp_home, projects_root, roots_file, make_session):
        """MOVE: task dir is copied from src key to dst key."""
        src_dir, _, uuid = make_session("srcproj", "20260503-src")
        dst_cwd = projects_root / "dstproj"
        dst_cwd.mkdir()
        src_task_dir, task_ids = self._make_task_files(tmp_home, src_dir)

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"

        dst_session_dir = dst_cwd / "cc-sessions" / "20260503-src"
        dst_task_dir = self._dst_task_dir(tmp_home, dst_session_dir)
        assert dst_task_dir.is_dir(), f"dst task dir not created: {dst_task_dir}"
        actual_ids = {p.stem for p in dst_task_dir.glob("*.json")}
        assert actual_ids == set(task_ids)

    def test_task_files_migrated_on_rename(
            self, tmp_home, projects_root, roots_file, make_session):
        """RENAME: task dir is copied from old-tag key to new-tag key."""
        src_dir, _, uuid = make_session("myproj", "20260503-oldtag")
        src_task_dir, task_ids = self._make_task_files(tmp_home, src_dir, count=2)

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--rename-tag", "20260503-newtag",
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"

        dst_session_dir = src_dir.parent.parent / "cc-sessions" / "20260503-newtag"
        dst_task_dir = self._dst_task_dir(tmp_home, dst_session_dir)
        assert dst_task_dir.is_dir(), f"dst task dir not created: {dst_task_dir}"
        actual_ids = {p.stem for p in dst_task_dir.glob("*.json")}
        assert actual_ids == set(task_ids)

    def test_dry_run_reports_task_count_and_paths(
            self, tmp_home, projects_root, roots_file, make_session):
        """Dry-run: count and both paths printed; nothing created on disk."""
        src_dir, _, uuid = make_session("srcproj", "20260503-src")
        dst_cwd = projects_root / "dstproj"
        dst_cwd.mkdir()
        src_task_dir, _ = self._make_task_files(tmp_home, src_dir, count=3)

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uuid,
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0
        combined = out + err
        assert "3" in combined
        assert str(src_task_dir) in combined

        dst_session_dir = dst_cwd / "cc-sessions" / "20260503-src"
        dst_task_dir = self._dst_task_dir(tmp_home, dst_session_dir)
        assert not dst_task_dir.exists(), "dry-run must not create dst task dir"

    def test_no_task_dir_silently_skipped_on_execute(
            self, tmp_home, projects_root, roots_file, make_session):
        """If src task dir does not exist, execute succeeds with no task dir created."""
        src_dir, _, uuid = make_session("srcproj", "20260503-src")
        dst_cwd = projects_root / "dstproj"
        dst_cwd.mkdir()

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"

        dst_session_dir = dst_cwd / "cc-sessions" / "20260503-src"
        dst_task_dir = self._dst_task_dir(tmp_home, dst_session_dir)
        assert not dst_task_dir.exists()

    def test_task_dir_clobber_aborts_before_any_copy(
            self, tmp_home, projects_root, roots_file, make_session):
        """If dst task dir already exists, abort (consistent with clobber check)."""
        src_dir, _, uuid = make_session("srcproj", "20260503-src")
        dst_cwd = projects_root / "dstproj"
        dst_cwd.mkdir()
        self._make_task_files(tmp_home, src_dir, count=2)

        dst_session_dir = dst_cwd / "cc-sessions" / "20260503-src"
        dst_task_dir = self._dst_task_dir(tmp_home, dst_session_dir)
        dst_task_dir.mkdir(parents=True)  # pre-existing

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc != 0
        combined = out + err
        assert "ABORT" in combined
        # cc-sessions dir must NOT have been copied either - abort is pre-flight
        assert not (dst_cwd / "cc-sessions" / "20260503-src").exists()

    def test_cleanup_script_contains_task_dir_removal(
            self, tmp_home, projects_root, roots_file, make_session, tmp_path):
        """After execute, the cleanup script includes rm -rf of the src task dir."""
        src_dir, _, uuid = make_session("srcproj", "20260503-src")
        dst_cwd = projects_root / "dstproj"
        dst_cwd.mkdir()
        src_task_dir, _ = self._make_task_files(tmp_home, src_dir, count=1)

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"

        # Find the cleanup script path from output
        cleanup_path = None
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("bash ") and "/tmp/move-session-cleanup-" in stripped:
                cleanup_path = Path(stripped.split(None, 1)[1])
                break
        assert cleanup_path and cleanup_path.is_file(), (
            f"cleanup script not found in output:\n{out}"
        )
        script_text = cleanup_path.read_text()
        assert str(src_task_dir) in script_text
        assert "rm -rf" in script_text


class TestEmptyJsonlRefusedEarly:
    """Item 5.4.3: when tombstoning is on (default), an empty/malformed source
    jsonl must be refused before any copy happens, with a clear error pointing
    at --no-tombstone or fixing the source. With --no-tombstone, the same
    source must succeed because last_record is no longer needed."""

    def _empty_session(self, tmp_home, projects_root):
        """Make a session whose jsonl exists but has zero parseable records."""
        cwd = projects_root / "empty-proj"
        cwd.mkdir(parents=True, exist_ok=True)
        session_dir = cwd / "cc-sessions" / "20260503-empty"
        (session_dir / "working").mkdir(parents=True)
        (session_dir / "out").mkdir()
        encoded = str(cwd).replace("/", "-")
        key_dir = tmp_home / ".claude" / "projects" / encoded
        key_dir.mkdir(parents=True, exist_ok=True)
        import uuid as uuidlib
        uid = str(uuidlib.uuid4())
        jsonl = key_dir / f"{uid}.jsonl"
        # Truly empty: no records at all.
        jsonl.write_text("")
        return session_dir, jsonl, uid

    def test_refuses_early_with_clear_error(
            self, tmp_home, projects_root, roots_file):
        session_dir, _, uid = self._empty_session(tmp_home, projects_root)
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()
        rc, out, err = _run(
            "--src-session", str(session_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc != 0
        combined = out + err
        assert "no parseable records" in combined
        assert "--no-tombstone" in combined
        # Critical: refused EARLY - destination cc-sessions dir must not exist.
        assert not (dst_cwd / "cc-sessions").exists()

    def test_no_tombstone_flag_lets_empty_source_succeed(
            self, tmp_home, projects_root, roots_file):
        session_dir, _, uid = self._empty_session(tmp_home, projects_root)
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()
        rc, out, err = _run(
            "--src-session", str(session_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uid,
            "--no-tombstone",
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"
        assert (dst_cwd / "cc-sessions" / "20260503-empty").is_dir()


class TestTagFileDiscovery:
    """Fix A: discover_session_jsonl must resolve the correct jsonl via the
    .tag file when a RENAME has been performed but /rename hasn't run inside
    CC yet (so custom_titles still contains the old tag, not the new one).

    Scenario reproduced from the double-move investigation:
      1. Two sessions exist on the same date (date-prefix fallback ambiguous).
      2. One session is RENAME'd (writes .tag file, doesn't touch custom_titles).
      3. A MOVE of the renamed session must succeed without --uuid."""

    def test_move_after_rename_resolves_via_tag_file(
            self, tmp_home, projects_root, roots_file, make_session):
        """MOVE after RENAME (no /rename in CC): .tag file lookup resolves the
        correct jsonl even when multiple same-date sessions exist."""
        # Two sessions from the same date so date-prefix fallback is ambiguous.
        src_dir_alpha, jsonl_alpha, uuid_alpha = make_session(
            "src-proj", "20260601-alpha")
        _, jsonl_beta, _ = make_session(
            "src-proj", "20260601-beta")

        # Simulate RENAME: copy cc-sessions dir, write .tag file, leave
        # custom_titles unchanged (no /rename run in CC).
        src_cwd = src_dir_alpha.parent.parent
        renamed_dir = src_cwd / "cc-sessions" / "20260601-alpha-renamed"
        renamed_dir.mkdir(parents=True)
        (renamed_dir / "working").mkdir()
        (renamed_dir / "out").mkdir()

        key_dir = jsonl_alpha.parent
        tag_file = key_dir / f"{uuid_alpha}.tag"
        tag_file.write_text("alpha-renamed\n")
        # custom_titles in the jsonl still has the OLD name: fine, that's the
        # bug scenario. The .tag file is the only hint.

        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()

        rc, out, err = _run(
            "--src-session", str(renamed_dir),
            "--dst-cwd", str(dst_cwd),
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, (
            f"MOVE after RENAME failed; .tag file lookup did not work.\n"
            f"stdout:\n{out}\nstderr:\n{err}"
        )
        # Correct uuid (alpha) was used, not beta.
        dst_key_dir = tmp_home / ".claude" / "projects" / str(dst_cwd).replace("/", "-")
        assert (dst_key_dir / f"{uuid_alpha}.jsonl").is_file(), (
            f"Expected alpha uuid {uuid_alpha} in dst, but not found"
        )

    def test_single_session_still_works_without_tag_file(
            self, tmp_home, projects_root, roots_file, make_session):
        """Regression: with only one session in the project key (single-candidate
        path, step 2), the .tag file step is skipped. Must still succeed."""
        src_dir, _, uuid = make_session("proj", "20260601-solo")
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"
        assert (dst_cwd / "cc-sessions" / "20260601-solo").is_dir()


class TestTombstoneChainWording:
    """Fix B: TOMBSTONE.md and jsonl tombstone notice must include a
    chain-traversal note so users following a double-moved session
    know to follow the TOMBSTONE.md chain."""

    def test_tombstone_md_includes_chain_note(
            self, tmp_home, projects_root, roots_file, make_session):
        """TOMBSTONE.md written by a MOVE must include the chain-traversal note."""
        src_dir, _, uuid = make_session("src-proj", "20260601-source")
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"

        tombstone_md = src_dir / "TOMBSTONE.md"
        assert tombstone_md.is_file()
        text = tombstone_md.read_text()
        assert "follow the chain" in text, (
            f"TOMBSTONE.md missing chain-traversal note:\n{text}"
        )
        assert "TOMBSTONE.md" in text

    def test_jsonl_tombstone_notice_includes_chain_note(
            self, tmp_home, projects_root, roots_file, make_session):
        """The jsonl tombstone user-record content must include the chain note."""
        src_dir, src_jsonl, uuid = make_session("src-proj", "20260601-source")
        dst_cwd = projects_root / "dst-proj"
        dst_cwd.mkdir()

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--dst-cwd", str(dst_cwd),
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"

        # Read the appended tombstone records from the source jsonl.
        records = [json.loads(l) for l in src_jsonl.read_text().splitlines() if l.strip()]
        tombstone_user = next(
            (r for r in records if r.get("type") == "user" and "[TOMBSTONE]" in
             (r.get("message", {}).get("content", "") or "")),
            None,
        )
        assert tombstone_user is not None, "No tombstone user record found in source jsonl"
        notice_text = tombstone_user["message"]["content"]
        assert "follow the chain" in notice_text, (
            f"Jsonl tombstone notice missing chain note:\n{notice_text}"
        )

    def test_rename_tombstone_md_includes_chain_note(
            self, tmp_home, projects_root, roots_file, make_session):
        """RENAME-only TOMBSTONE.md must also include the chain-traversal note."""
        src_dir, _, uuid = make_session("proj", "20260601-old-tag")

        rc, out, err = _run(
            "--src-session", str(src_dir),
            "--rename-tag", "20260601-new-tag",
            "--uuid", uuid,
            "--execute",
            env={"HOME": str(tmp_home)},
        )
        assert rc == 0, f"{err}\n{out}"

        tombstone_md = src_dir / "TOMBSTONE.md"
        assert tombstone_md.is_file()
        text = tombstone_md.read_text()
        assert "follow the chain" in text, (
            f"RENAME TOMBSTONE.md missing chain note:\n{text}"
        )
