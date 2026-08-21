# `pm-project-layout-reference` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `pm-project-layout-reference` skill (canonical `~/cc/<project>` folder
conventions), real content for the two placeholder pdata-migration prompts, and a new
`ccst pdata reorganize` command that performs a flat-to-nested folder split while keeping
pdata's own `file_path` references correct.

**Architecture:** A new reference-only skill under `src/cc_session_tools/skills/`. A new
`lib/pdata/reorganize.py` module (`dry_run()`/`write()`, mirroring `init_service.py`'s shape:
same backup reuse, same rollback contract) plus a `service.find_records_by_file_path_prefix()`
helper it depends on. A new `ccst pdata reorganize` subcommand wraps it. The two prompt files
get real, step-by-step content replacing their placeholders.

**Tech Stack:** Python 3.13, pytest + subprocess-based CLI tests (matching `tests/pdata/` and
`tests/test_ccst_pdata_init_cli.py` conventions).

Spec: `docs/superpowers/specs/2026-08-21-pm-project-layout-reference-design.md`

Work happens in the worktree already created for this:
```sh
cd /home/chris/repos/claude-code-session-tools/.worktrees/pm-project-layout-reference
uv sync --extra dev   # if not already done
```
(Branch `f/v2.8.1`, off `main` — already checked out in that worktree.)

---

### Task 1: `pm-project-layout-reference` skill

**Files:**
- Create: `src/cc_session_tools/skills/pm-project-layout-reference/SKILL.md`

- [ ] **Step 1: Write `SKILL.md`**

Frontmatter: use the spec's draft YAML verbatim (design §1) as the `name`/`description`.

Body content, translating the spec's design §1 into skill prose (second person, imperative,
matching `pm-project-init/SKILL.md`'s tone):

1. A short intro: this is the canonical reference for `~/cc/<project>` folder conventions -
   read before setting up a new project's folders, reorganising an existing one, or deciding
   whether a folder needs splitting.
2. The folder table (five folders + purpose) from the spec, with the "not exhaustive - a
   project may have its own domain-specific folders (`evidence/`, `costs/`, `filings/`,
   `sources/`, `data/`)" note.
3. The general nesting criteria section verbatim in substance: >500 files in a single folder
   triggers subdividing; date-based for `correspondence/`, topic-based otherwise; the
   new-project "ask, don't guess" fallback with the example question; the `pbt` local/imported
   note.
4. The workstream lifecycle section (numbering, archival, the "no project has
   `workstreams-archived/` yet" honesty note so this doesn't get cited as existing practice).
5. The folder-owned/db-owned relationship paragraph (these folders hold folder-owned content;
   orthogonal to whether a project is pdata-migrated; `home`+`pod` as the two contrasting
   examples).
6. A closing section: "To actually perform a flat-to-nested split, use
   `ccst pdata reorganize --project <name> --folder <folder> --strategy by-year` (dry-run
   first, `--write` to apply) - see its `--help` for details. Anything simpler (renaming one
   folder, adding a new folder type) is a plain `git mv`/`mv`, no tool needed."

- [ ] **Step 2: Confirm the skill is discoverable via ccst's existing bundling**

Run: `uv run pytest tests/test_ccst_bundle_discovery.py tests/pdata/ -q` (no code changed yet,
this just confirms nothing already breaks before Task 1's file addition is exercised by a real
test in Task 2 onward - skills aren't individually pytested, they're package-data).

- [ ] **Step 3: Commit**

```bash
git add src/cc_session_tools/skills/pm-project-layout-reference/SKILL.md
git commit -m "docs(skills): add pm-project-layout-reference reference skill"
```

---

### Task 2: `service.find_records_by_file_path_prefix()`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/service.py`
- Test: `tests/pdata/test_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_find_records_by_file_path_prefix_matches_across_groups(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    service.add_record(
        project="demo", record_group="letters", content="a",
        file_path="correspondence/a.md", fields={}, created_at=1,
    )
    service.add_record(
        project="demo", record_group="notes", content="b",
        file_path="correspondence/b.md", fields={}, created_at=1,
    )
    service.add_record(
        project="demo", record_group="letters", content="c",
        file_path="analysis/c.md", fields={}, created_at=1,
    )

    matches = service.find_records_by_file_path_prefix(project="demo", prefix="correspondence/")

    assert sorted(r.file_path for r in matches) == ["correspondence/a.md", "correspondence/b.md"]


def test_find_records_by_file_path_prefix_empty_project_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    # no records ever added for "demo" - no .db file exists at all yet
    assert service.find_records_by_file_path_prefix(project="demo", prefix="correspondence/") == []
```

Check `tests/pdata/test_service.py`'s existing imports/fixtures first (likely already imports
`store` and uses `monkeypatch.setenv` the same way) and match its exact style rather than
introducing a new fixture pattern.

- [ ] **Step 2: Run to verify red**

Run: `uv run pytest tests/pdata/test_service.py -k find_records_by_file_path_prefix -v`

- [ ] **Step 3: Implement**

Add to `service.py`, near `query_records`:

```python
def find_records_by_file_path_prefix(*, project: str, prefix: str) -> list[Record]:
    """Every active record across every record_group in this project whose file_path starts
    with prefix - used by lib/pdata/reorganize.py to find which rows need their file_path
    updated when a folder is split into a nested structure. Returns [] both when the project
    has no .db yet and when it has one but nothing matches - callers that need to distinguish
    "no store" from "store, no match" should check store.db_path(project).exists() themselves.

    Known limitation: prefix is interpolated into a SQL LIKE pattern unescaped, so a literal
    '%' or '_' in it would be interpreted as a wildcard rather than a literal character. This
    repo's query_records()/_parse_where_clause() plumbing (shared with `ccst pdata query`)
    has no ESCAPE-clause support to fix this properly without changing that shared code, which
    is out of scope here. Not a practical issue for this callsite specifically - prefix is
    always a project-relative folder path (this codebase's own naming convention uses hyphens,
    not underscores, e.g. ws-01-slug), but worth knowing if this function is ever reused
    somewhere prefix isn't a controlled folder name."""
    if not store.db_path(project).exists():
        return []
    matches: list[Record] = []
    for group in schema_list(project=project):
        matches.extend(
            query_records(
                project=project, record_group=group["record_group"],
                # No surrounding quotes: service._parse_where_clause's `value` capture group
                # is bound directly as the SQL parameter, not SQL-literal syntax to be
                # unquoted. Quoting it here would make the comparison look for a file_path
                # that literally starts and ends with an apostrophe - never matching anything.
                where=[f"file_path LIKE {prefix}%"],
            )
        )
    return matches
```

Add `from cc_session_tools.lib.pdata import store` to this file's imports if not already
present (check first - `store` may already be imported under a different alias or not at all).

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/pdata/test_service.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/service.py tests/pdata/test_service.py
git commit -m "feat(pdata): add find_records_by_file_path_prefix for cross-group file_path lookup"
```

---

### Task 3: `lib/pdata/reorganize.py` - dry-run planning

**Files:**
- Create: `src/cc_session_tools/lib/pdata/reorganize.py`
- Test: `tests/pdata/test_reorganize.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import re
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import reorganize, service, store


def test_dry_run_computes_by_year_moves_from_filename_dates(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-1030--A-to-B--email.md").write_text("x")
    (corr / "2026.01.02-0900--C-to-D--email.md").write_text("y")

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    moves = {m.old_relative: m.new_relative for m in plan.moves}
    assert moves["correspondence/2025.03.14-1030--A-to-B--email.md"] == \
        "correspondence/2025/2025.03.14-1030--A-to-B--email.md"
    assert moves["correspondence/2026.01.02-0900--C-to-D--email.md"] == \
        "correspondence/2026/2026.01.02-0900--C-to-D--email.md"
    assert plan.matched_records == []
    assert plan.external_references == []


def test_dry_run_falls_back_to_mtime_when_no_leading_date(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    f = corr / "no-date-in-name.md"
    f.write_text("x")

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    # mtime-derived year - just assert it landed under *some* four-digit year folder,
    # not a specific one (avoids a flaky test pinned to "this year").
    (move,) = plan.moves
    assert re.fullmatch(r"correspondence/\d{4}/no-date-in-name\.md", move.new_relative)


def test_dry_run_finds_matching_pdata_records(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    service.add_record(
        project="demo", record_group="letters", content="x",
        file_path="correspondence/2025.03.14-note.md", fields={}, created_at=1,
    )

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    assert len(plan.matched_records) == 1
    record, new_path = plan.matched_records[0]
    assert new_path == "correspondence/2025/2025.03.14-note.md"


def test_dry_run_reports_external_references_without_moving_anything(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    (project_root / "CLAUDE.md").write_text(
        "See correspondence/2025.03.14-note.md for the full record.\n"
    )

    plan = reorganize.dry_run(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    assert len(plan.external_references) == 1
    ref = plan.external_references[0]
    assert ref.file == project_root / "CLAUDE.md"
    assert "correspondence/2025.03.14-note.md" in ref.line_text
    # Nothing moved - dry_run never touches the filesystem or the DB.
    assert (corr / "2025.03.14-note.md").exists()


def test_dry_run_rejects_unknown_strategy(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    project_root = tmp_path / "projects" / "demo"
    (project_root / "correspondence").mkdir(parents=True)

    with pytest.raises(ValueError, match="strategy"):
        reorganize.dry_run(
            project="demo", project_root=project_root, folder="correspondence",
            strategy="by-topic",
        )


def test_dry_run_rejects_missing_folder(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="correspondence"):
        reorganize.dry_run(
            project="demo", project_root=project_root, folder="correspondence",
            strategy="by-year",
        )


def test_dry_run_rejects_absolute_folder_path(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="absolute path"):
        reorganize.dry_run(
            project="demo", project_root=project_root, folder="/etc",
            strategy="by-year",
        )


def test_dry_run_rejects_folder_with_parent_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="path-traversal"):
        reorganize.dry_run(
            project="demo", project_root=project_root, folder="../../etc",
            strategy="by-year",
        )
```

- [ ] **Step 2: Run to verify red**

Run: `uv run pytest tests/pdata/test_reorganize.py -v`

- [ ] **Step 3: Implement `reorganize.py`**

```python
"""Flat-to-nested folder restructuring for `ccst pdata reorganize` (design spec
2026-08-21-pm-project-layout-reference-design.md, §3). Scoped to exactly one operation:
splitting a flat folder into year or year/month subfolders once it's grown past the
`pm-project-layout-reference` skill's 500-file guidance - not general-purpose reorganisation.

dry_run() never touches the filesystem or the database; write() (Task 4) performs the moves
and DB updates dry_run() planned, reusing backup.create_backup() as its safety net exactly
the way `ccst pdata init --write` does.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata import init_paths, service
from cc_session_tools.lib.pdata.service import Record

_LEADING_DATE_RE = re.compile(r"^(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})-")

_STRATEGIES = frozenset({"by-year", "by-year-month"})


@dataclass(frozen=True)
class Move:
    old_relative: str
    new_relative: str


@dataclass(frozen=True)
class ExternalReference:
    file: Path
    line_number: int
    line_text: str


@dataclass(frozen=True)
class ReorganizePlan:
    project: str
    project_root: Path
    folder: str
    strategy: str
    moves: list[Move]
    matched_records: list[tuple[Record, str]]  # (record, new_file_path)
    external_references: list[ExternalReference]


def _year_for(path: Path) -> str:
    match = _LEADING_DATE_RE.match(path.name)
    if match:
        return match.group("year")
    return time.strftime("%Y", time.localtime(path.stat().st_mtime))


def _year_month_for(path: Path) -> str:
    match = _LEADING_DATE_RE.match(path.name)
    if match:
        return f"{match.group('year')}/{match.group('month')}"
    return time.strftime("%Y/%m", time.localtime(path.stat().st_mtime))


def _new_relative(folder: str, entry: Path, strategy: str) -> str:
    subdir = _year_for(entry) if strategy == "by-year" else _year_month_for(entry)
    return f"{folder}/{subdir}/{entry.name}"


def _scan_external_references(
    *, project_root: Path, folder: str, old_relatives: list[str],
) -> list[ExternalReference]:
    """Grep every text file in project_root (excluding folder itself and the usual
    ccst/git bookkeeping dirs) for a literal occurrence of any old_relatives entry - reported
    only, never edited (design spec §3: rewriting prose isn't safe to automate).

    folder is excluded by path-prefix comparison, not single-component name matching - folder
    can itself be multi-segment (e.g. "workstreams/ws-01"), which a plain
    `part in excluded_names` check would never match against any single path component.

    Reuses init_paths.EXCLUDED_DIR_NAMES - the same set classify.py's own directory walk
    already excludes - rather than hardcoding a second, incomplete copy: a hardcoded
    ".pdata-migrated" literal here would also miss REHEARSAL_DB_DIRNAME/
    REHEARSAL_BACKUP_DIRNAME, walking into a rehearsal sandbox's own contents on any project
    that has ever used --rehearse and reporting spurious matches from inside it."""
    excluded_dir_names = init_paths.EXCLUDED_DIR_NAMES
    folder_parts = Path(folder).parts
    refs: list[ExternalReference] = []
    for candidate in sorted(project_root.rglob("*")):
        if not candidate.is_file():
            continue
        rel_parts = candidate.relative_to(project_root).parts
        if rel_parts[:len(folder_parts)] == folder_parts:
            continue  # inside the folder being reorganized itself
        if any(part in excluded_dir_names for part in rel_parts[:-1]):
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for old_relative in old_relatives:
                if old_relative in line:
                    refs.append(ExternalReference(
                        file=candidate, line_number=line_number, line_text=line.strip(),
                    ))
    return refs


def _validate_relative_folder(folder: str) -> None:
    """Same boundary guard as service._validate_relative_file_path, applied to --folder:
    without it, an absolute path silently discards project_root entirely when joined with `/`
    (pathlib's own behaviour, not a bug in this code - `Path("/a") / "/etc"` is `Path("/etc")`),
    and a '..' segment can escape project_root the same way a crafted --file could."""
    if folder.startswith("/"):
        raise ValueError(f"--folder must be relative to the project root, got absolute path: {folder!r}")
    if any(segment == ".." for segment in folder.split("/")):
        raise ValueError(f"--folder must not contain '..' path-traversal segments: {folder!r}")


def dry_run(*, project: str, project_root: Path, folder: str, strategy: str) -> ReorganizePlan:
    if strategy not in _STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r} - must be one of {sorted(_STRATEGIES)}")
    _validate_relative_folder(folder)
    folder_path = project_root / folder
    if not folder_path.is_dir():
        raise FileNotFoundError(f"no such folder to reorganize: {folder_path}")

    entries = sorted(p for p in folder_path.iterdir() if p.is_file())
    moves = [
        Move(
            old_relative=f"{folder}/{entry.name}",
            new_relative=_new_relative(folder, entry, strategy),
        )
        for entry in entries
    ]

    matched = service.find_records_by_file_path_prefix(project=project, prefix=f"{folder}/")
    move_by_old = {m.old_relative: m.new_relative for m in moves}
    matched_records = [
        (record, move_by_old[record.file_path])
        for record in matched
        if record.file_path in move_by_old
    ]

    external_references = _scan_external_references(
        project_root=project_root, folder=folder,
        old_relatives=[m.old_relative for m in moves],
    )

    return ReorganizePlan(
        project=project, project_root=project_root, folder=folder, strategy=strategy,
        moves=moves, matched_records=matched_records, external_references=external_references,
    )
```

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/pdata/test_reorganize.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/reorganize.py tests/pdata/test_reorganize.py
git commit -m "feat(pdata): add reorganize.dry_run() - flat-to-nested move planning"
```

---

### Task 4: `reorganize.write()` - perform the split

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/reorganize.py`
- Test: `tests/pdata/test_reorganize.py`

- [ ] **Step 1: Write the failing tests**

Add `import subprocess` and `from cc_session_tools.lib.pdata import backup` to
`tests/pdata/test_reorganize.py`'s existing imports (the tests below use `backup.BACKUP_DIR_ENV`
and `result.backup_path`, neither reachable from Task 3's `reorganize, service, store` imports
alone).

```python
import subprocess


def test_write_moves_files_and_updates_matching_records(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    record = service.add_record(
        project="demo", record_group="letters", content="x",
        file_path="correspondence/2025.03.14-note.md", fields={}, created_at=1,
    )

    result = reorganize.write(
        project="demo", project_root=project_root, folder="correspondence",
        strategy="by-year",
    )

    assert result.failure is None
    assert not (corr / "2025.03.14-note.md").exists()
    assert (corr / "2025" / "2025.03.14-note.md").exists()
    updated = service.list_records(project="demo", record_group="letters")[0]
    assert updated.file_path == "correspondence/2025/2025.03.14-note.md"
    assert updated.version == record.version + 1
    assert result.backup_path.exists()


def test_write_uses_git_mv_when_project_root_is_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project_root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=project_root, check=True)

    reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                      strategy="by-year")

    # write() only stages the rename via `git mv` - it never commits - so this checks the
    # staged rename directly rather than `git log --follow`, which walks committed history
    # only and would see nothing yet.
    status = subprocess.run(["git", "status", "--short"],
                            cwd=project_root, capture_output=True, text=True, check=True)
    assert "correspondence/2025.03.14-note.md" in status.stdout
    assert "correspondence/2025/2025.03.14-note.md" in status.stdout


def test_write_rolls_back_moved_files_on_record_update_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    service.add_record(
        project="demo", record_group="letters", content="x",
        file_path="correspondence/2025.03.14-note.md", fields={}, created_at=1,
    )

    # Force a version conflict: bump the record's version out from under write() by
    # updating it again before write() gets to its own update_record() call. Simplest way to
    # simulate this without threads: monkeypatch service.update_record to raise once.
    real_update = service.update_record
    calls = {"n": 0}
    def _flaky_update(**kwargs):
        calls["n"] += 1
        raise service.VersionConflictError(current={}, attempted={})
    monkeypatch.setattr(reorganize.service, "update_record", _flaky_update)

    result = reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                              strategy="by-year")

    assert result.failure is not None
    assert calls["n"] == 1
    # Rolled back: the file is back at its original flat location, not left half-moved.
    assert (corr / "2025.03.14-note.md").exists()
    assert not (corr / "2025").exists()


def test_write_reports_structured_failure_when_backup_fails(tmp_path, monkeypatch):
    """Matches init_service.write()'s own contract for this exact call: a backup failure
    must become a ReorganizeResult(failure=...), not an uncaught BackupError - nothing has
    been moved yet at this point, so there's nothing to roll back."""
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    def _raise(*args, **kwargs):
        raise backup.BackupError("simulated backup failure")
    monkeypatch.setattr(reorganize.backup, "create_backup", _raise)

    result = reorganize.write(project="demo", project_root=project_root, folder="correspondence",
                              strategy="by-year")

    assert result.failure is not None
    assert result.backup_path is None
    assert (corr / "2025.03.14-note.md").exists()  # untouched - failure was before any move
```

- [ ] **Step 2: Run to verify red**

Run: `uv run pytest tests/pdata/test_reorganize.py -k write -v`

- [ ] **Step 3: Implement `write()`**

Add to `reorganize.py` (imports to add: `subprocess`, `from cc_session_tools.lib.pdata import
backup`):

```python
@dataclass(frozen=True)
class ReorganizeFailure:
    reasons: list[str]


@dataclass(frozen=True)
class ReorganizeResult:
    plan: ReorganizePlan
    backup_path: Path | None
    failure: ReorganizeFailure | None


def _is_git_repo(project_root: Path) -> bool:
    return (project_root / ".git").exists()


def _move_file(*, project_root: Path, move: Move, use_git: bool) -> None:
    src = project_root / move.old_relative
    dest = project_root / move.new_relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    if use_git:
        subprocess.run(
            ["git", "mv", move.old_relative, move.new_relative],
            cwd=project_root, check=True, capture_output=True, text=True,
        )
    else:
        src.rename(dest)


def _move_file_back(*, project_root: Path, move: Move, folder: str, use_git: bool) -> None:
    """Undo _move_file - used by write()'s rollback path only. Also removes every nested
    subdirectory _move_file created that's now empty, walking up from the immediate parent -
    by-year-month leaves two levels (correspondence/2025/06/), not just one, so a single
    rmdir() of the immediate parent alone would leave the year directory behind."""
    dest_parent = (project_root / move.new_relative).parent
    folder_path = project_root / folder
    if use_git:
        subprocess.run(
            ["git", "mv", move.new_relative, move.old_relative],
            cwd=project_root, check=True, capture_output=True, text=True,
        )
    else:
        (project_root / move.new_relative).rename(project_root / move.old_relative)
    current = dest_parent
    while current != folder_path and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def write(*, project: str, project_root: Path, folder: str, strategy: str) -> ReorganizeResult:
    plan = dry_run(project=project, project_root=project_root, folder=folder, strategy=strategy)
    use_git = _is_git_repo(project_root)

    # Matches init_service.write()'s own handling of this exact call (init_service.py
    # ~lines 220-232): create_backup() can exhaust its retries and raise BackupError, which
    # must become a structured failure here too, not an uncaught crash before anything has
    # been moved.
    try:
        backup_path = backup.create_backup(project=project, project_root=project_root)
    except backup.BackupError as exc:
        return ReorganizeResult(plan=plan, backup_path=None, failure=ReorganizeFailure(reasons=[str(exc)]))

    moved: list[Move] = []
    try:
        for move in plan.moves:
            _move_file(project_root=project_root, move=move, use_git=use_git)
            moved.append(move)

        for record, new_file_path in plan.matched_records:
            service.update_record(
                project=project, record_id=record.id, expected_version=record.version,
                content=None, file_path=new_file_path, fields={},
            )
    except (OSError, subprocess.CalledProcessError, service.VersionConflictError,
            service.RecordNotFoundError) as exc:
        for move in reversed(moved):
            _move_file_back(project_root=project_root, move=move, folder=folder, use_git=use_git)
        return ReorganizeResult(
            plan=plan, backup_path=backup_path,
            failure=ReorganizeFailure(reasons=[str(exc)]),
        )

    return ReorganizeResult(plan=plan, backup_path=backup_path, failure=None)
```

Note the DB-update rollback gap this leaves, and don't paper over it: if a *later* record's
`update_record()` call fails after an *earlier* one already succeeded in this same `write()`
call, the file moves are rolled back but the earlier record's `file_path` is left pointing at
the new (now-reverted) location - a real but narrow edge case (multiple matched records, one
conflicts partway through). Flag this as a known follow-up rather than silently shipping it
unmentioned: add a code comment on the `except` block naming the gap, and a TODO.md entry
(Task 6 covers TODO.md). Closing it fully would mean also reversing already-applied
`update_record()` calls, which needs their prior file_path values kept around - reasonable
follow-up work, not blocking this plan's completion.

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/pdata/test_reorganize.py -q`

- [ ] **Step 5: Add the rollback-gap comment + TODO.md entry**

In `write()`'s `except` block, add:
```python
    except (OSError, subprocess.CalledProcessError, service.VersionConflictError,
            service.RecordNotFoundError) as exc:
        # Known gap: if plan.matched_records has more than one entry and a LATER one's
        # update_record() call fails here, EARLIER ones in this same loop already succeeded
        # and are not reversed - only the file moves are rolled back. See TODO.md.
        for move in reversed(moved):
```

Append to `TODO.md` (check the file's existing section style first and match it):
```markdown
## `ccst pdata reorganize` - full DB-update rollback

`reorganize.write()` rolls back file moves on failure but not already-applied
`update_record()` calls from earlier in the same run, if a later matched record's update
fails partway through a multi-record reorganisation. Needs each record's prior file_path kept
around to reverse. Low priority - the same `--write` run's own backup still lets a human
restore from the tarball if this narrow case bites.
```

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/pdata/reorganize.py tests/pdata/test_reorganize.py TODO.md
git commit -m "feat(pdata): add reorganize.write() - perform the flat-to-nested split"
```

---

### Task 5: `ccst pdata reorganize` CLI command

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Test: `tests/test_ccst_pdata_reorganize_cli.py` (new file, matching
  `tests/test_ccst_pdata_init_cli.py`'s conventions - reuse its `_run`/`base_env` pattern,
  check that file first rather than reinventing the harness)

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    env["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")
    return env


def test_reorganize_dry_run_prints_the_move_plan(base_env, tmp_path):
    corr = tmp_path / "projects" / "demo" / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-year")

    assert r.returncode == 0, r.stderr
    assert "correspondence/2025.03.14-note.md" in r.stdout
    assert "correspondence/2025/2025.03.14-note.md" in r.stdout
    assert (corr / "2025.03.14-note.md").exists()  # dry-run: nothing moved


def test_reorganize_write_moves_files(base_env, tmp_path):
    corr = tmp_path / "projects" / "demo" / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-year", "--write")

    assert r.returncode == 0, r.stderr
    assert not (corr / "2025.03.14-note.md").exists()
    assert (corr / "2025" / "2025.03.14-note.md").exists()


def test_reorganize_rejects_unknown_strategy(base_env, tmp_path):
    (tmp_path / "projects" / "demo" / "correspondence").mkdir(parents=True)

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-topic")

    assert r.returncode == 2
    assert "invalid choice" in r.stderr  # argparse choices=[...] rejects it before reorganize.py sees it


def test_reorganize_reports_external_references(base_env, tmp_path):
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    (project_root / "CLAUDE.md").write_text("See correspondence/2025.03.14-note.md.\n")

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-year")

    assert "CLAUDE.md" in r.stdout
    assert "correspondence/2025.03.14-note.md" in r.stdout
```

- [ ] **Step 2: Run to verify red**

Run: `uv run pytest tests/test_ccst_pdata_reorganize_cli.py -v`

- [ ] **Step 3: Implement** - add near `_cmd_pdata_init` in `ccst.py`:

```python
def _cmd_pdata_reorganize(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import init_paths, reorganize

    try:
        project_root = init_paths.resolve_project_root(args.project, rehearse=None)
    except ValueError as exc:
        # Matches _cmd_pdata_init's own handling of the identical resolve_project_root() call -
        # a bad --project name (e.g. containing '/') must give the same clean exit-2 message
        # every other pdata command gives, not an uncaught traceback.
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2

    try:
        if not args.write:
            plan = reorganize.dry_run(
                project=args.project, project_root=project_root,
                folder=args.folder, strategy=args.strategy,
            )
            for move in plan.moves:
                print(f"{move.old_relative} -> {move.new_relative}")
            for record, new_path in plan.matched_records:
                print(f"  pdata record {record.id} (group={record.record_group}): "
                      f"file_path -> {new_path}")
            for ref in plan.external_references:
                print(f"  external reference: {ref.file}:{ref.line_number}: {ref.line_text}")
            if not plan.moves:
                print(f"no files found directly under {args.folder}/")
            return 0

        result = reorganize.write(
            project=args.project, project_root=project_root,
            folder=args.folder, strategy=args.strategy,
        )
        if result.failure is not None:
            print("ccst pdata reorganize: failed, rolled back:", file=sys.stderr)
            for reason in result.failure.reasons:
                print(f"  - {reason}", file=sys.stderr)
            return 1

        print(f"Moved {len(result.plan.moves)} file(s) under {args.folder}/")
        print(f"Backup: {result.backup_path}")
        for ref in result.plan.external_references:
            print(f"  still needs manual review: {ref.file}:{ref.line_number}: {ref.line_text}")
        return 0
    except (FileNotFoundError, ValueError) as exc:
        print(f"ccst pdata reorganize: {exc}", file=sys.stderr)
        return 2
```

Argparse wiring - add near the `pdata init` subparser registration:

```python
    pdata_reorganize_parser = pdata_sub.add_parser(
        "reorganize", help="Split a flat folder into a nested (by-year) structure"
    )
    pdata_reorganize_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_reorganize_parser.add_argument("--folder", required=True, metavar="RELATIVE_PATH")
    pdata_reorganize_parser.add_argument(
        "--strategy", required=True, choices=["by-year", "by-year-month"],
    )
    pdata_reorganize_parser.add_argument(
        "--write", action="store_true",
        help="Perform the move and update matching pdata records (default: dry-run only)",
    )
```

And the dispatch line alongside `if args.verb == "verify":` (find the exact dispatch block for
`pdata init`/`pdata verify` first and match its shape):

```python
        if args.verb == "reorganize":
            sys.exit(_cmd_pdata_reorganize(args))
```

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_ccst_pdata_reorganize_cli.py -q`

- [ ] **Step 5: Full pdata suite**

Run: `uv run pytest tests/pdata/ tests/test_ccst_pdata_init_cli.py tests/test_ccst_pdata_reorganize_cli.py tests/test_ccst_bundle_discovery.py -q`

- [ ] **Step 6: mypy**

Run: `uv run mypy src/cc_session_tools/cli/ccst.py src/cc_session_tools/lib/pdata/reorganize.py src/cc_session_tools/lib/pdata/service.py`

- [ ] **Step 7: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_reorganize_cli.py
git commit -m "feat(pdata): wire ccst pdata reorganize CLI command"
```

---

### Task 6: Real content for the two pdata-migration prompts

**Files:**
- Modify: `src/cc_session_tools/prompts/pdata-migration-claude-md-update.md`
- Modify: `src/cc_session_tools/prompts/pdata-migration-skills-update.md`
- Modify: `tests/test_ccst_bundle_discovery.py` (extend the existing
  `test_discover_prompts_dir_finds_the_real_bundled_prompts` test, or add a sibling one)

- [ ] **Step 1: Write the failing test** - a regression guard so neither file can silently
  regress to placeholder text:

```python
def test_bundled_prompts_are_no_longer_placeholders():
    prompts_dir = ccst._discover_prompts_dir()
    for filename in ("pdata-migration-claude-md-update.md", "pdata-migration-skills-update.md"):
        text = (prompts_dir / filename).read_text()
        assert "PLACEHOLDER" not in text
```

- [ ] **Step 2: Run to verify red**

Run: `uv run pytest tests/test_ccst_bundle_discovery.py -k placeholders -v`

- [ ] **Step 3: Write `pdata-migration-claude-md-update.md`**

Structure, matching `docs/global-claude-md-bootstrap-prompt.md`'s precedent (context-check step
first, numbered steps, explicit idempotency notes):

1. **Context check**: confirm cwd is the target project (`~/cc/<project>`) - if not, print an
   error and stop, same shape as the bootstrap prompt's own Step 1.
2. **Read the project's `CLAUDE.md`** (and any other top-level `.md` docs) in full.
3. **Find references to the pre-migration flat-file layout** - literal paths, "the CSV file",
   "the data folder", anything describing where project data used to live before
   `ccst pdata init --write`.
4. **Update those references** to describe the pdata-backed store instead - point at
   `ccst pdata list`/`get`/`query` rather than a file path, note the record_group(s) involved.
   Idempotent: if a doc already describes the pdata store correctly, leave it alone.
5. **Check for folder-layout drift**: read `pm-project-layout-reference` skill; if this
   project's other folders (`correspondence/`, etc.) look out of step with its criteria (e.g. a
   flat folder over 500 files), **note it in the session's own summary to the user - do not
   restructure inline here**. Reorganising is a separate, deliberate pass
   (`ccst pdata reorganize`), not a side effect of a doc-update prompt.
6. **Report** what was changed (or confirm nothing needed changing).

- [ ] **Step 4: Write `pdata-migration-skills-update.md`**

Structure:

1. **Context check**, same shape.
2. **Find every Claude Code skill referencing this project's old flat-file paths**: search
   `~/.claude/skills/*/SKILL.md` (and any scripts under each skill's directory) for literal
   mentions of `~/cc/<project>/<old-path>` or relative equivalents.
3. **For each match**, determine whether the skill reads/writes that path directly or just
   mentions it in passing; for a direct read/write, rewrite it to use the equivalent
   `ccst pdata` command (`add`/`get`/`list`/`query`/`update`) instead of file I/O.
4. **Do not touch skills unrelated to this project** - match by literal path, not by skill name
   guesswork.
5. **Report** which skills were found and updated, and which (if any) were found but need a
   human decision (e.g. a skill doing something more complex than a simple read/write that
   isn't safe to rewrite mechanically).

- [ ] **Step 5: Run to verify green**

Run: `uv run pytest tests/test_ccst_bundle_discovery.py -q`

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/prompts/pdata-migration-claude-md-update.md \
        src/cc_session_tools/prompts/pdata-migration-skills-update.md \
        tests/test_ccst_bundle_discovery.py
git commit -m "docs(pdata): replace the two migration prompts' placeholder content with real steps"
```

---

### Task 7: Full verification, CHANGELOG, version bump

- [ ] Run: `uv run pytest -q` - full suite green.
- [ ] Run: `uv run mypy src/cc_session_tools/lib/pdata/ src/cc_session_tools/cli/ccst.py`
- [ ] `uv build` then `unzip -l dist/*.whl | grep -E "skills/pm-project-layout-reference|prompts/"`
  - confirm the new skill and both prompt files are actually bundled into the wheel.
- [ ] CHANGELOG.md - Chris named this branch/release `v2.8.1` explicitly when creating
  `f/v2.8.1`, ahead of this plan; use that version rather than deriving one from the version
  policy (new-feature content would otherwise suggest a minor bump - flag this to Chris only if
  it matters to him, don't silently second-guess his stated version). New
  `## [2.8.1] - <date>` section under `[Unreleased]`, `### Added`:
  - `ccst pdata reorganize --project <name> --folder <folder> --strategy by-year|by-year-month
    [--write]` - splits a flat folder into a nested structure, keeping matching pdata records'
    `file_path` correct, backing up first.
  - New `pm-project-layout-reference` skill - canonical `~/cc/<project>` folder conventions.
  - The two pdata-migration prompts (`pdata-migration-claude-md-update.md`,
    `pdata-migration-skills-update.md`) now have real content instead of placeholders.
- [ ] Bump `pyproject.toml`'s `version` to match (2.8.1), `uv sync --extra dev` to update
  `uv.lock` to match.
- [ ] Manual smoke test: run a real `ccst pdata reorganize --project <fixture> --folder
  correspondence --strategy by-year` dry-run then `--write` against a scratch project with a
  handful of dated files and at least one pdata record referencing one of them - confirm the
  printed plan, the actual move, the updated `file_path` (via `ccst pdata list`), and the
  backup file all look right.
- [ ] Commit: `git add CHANGELOG.md pyproject.toml uv.lock && git commit -m "chore(release):
  bump to 2.8.1 for pm-project-layout-reference + ccst pdata reorganize"`

### Task 8: Code review + ship

- [ ] Run `/code-review medium` (or equivalent) on the full branch diff; fix anything real it
  finds, re-run the affected tests.
- [ ] Push `f/v2.8.1`, open a PR against `main` (per this repo's normal PR flow - this branch
  was deliberately created off `main`, not off a continuation branch, per Chris's instruction),
  wait for CI (`.github/workflows/ci.yml` runs on PRs to `main`), merge, delete the remote
  branch, fast-forward local `main`, remove the worktree.
- [ ] Tag `v2.8.1` and push tags per this repo's `CLAUDE.md` release process - Chris has already
  said this branch's work should be tagged as the `v2.8.1` release once done, so this step is
  pre-authorised; still confirm the PR has actually merged to `main` first before tagging.
