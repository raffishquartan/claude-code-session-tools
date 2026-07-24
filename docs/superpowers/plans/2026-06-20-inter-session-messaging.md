# Inter-session Messaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Update (2026-07-13):** the message store described below has since moved to `ccmsg.db`
> (SQLite) under `~/.local/share/claude/` — see
> `2026-07-13-data-store-uplift-00-overview.md` and `2026-07-13-data-store-uplift-02-ccmsg.md`
> for the current layout. This document is left as-written for historical accuracy.

**Goal:** Let one Claude Code session leave a durable, addressed, auditable message for another session (a specific session, a whole project, or "whoever is working on X"), delivered without the user prompting, with read-receipts, claims, and rename/move safety — all shipped inside CCST.

**Architecture:** A markdown-with-frontmatter message store under `~/.claude/cc-messages/`, partitioned by the recipient's working-directory location (project / repo / other-path / global). A new `ccmsg` CLI does all read/write/filter logic; two delivery hooks (`SessionStart` full sweep, `UserPromptSubmit` incremental sweep) call a single shared `deliver()` and inject a compact digest as `additionalContext`. A new `send-session-message` skill guides composition/addressing, a new `ccst claude-md` primitive maintains a managed block in the global CLAUDE.md, and the existing `move-session` skill gains rename/move safety for messages.

**Tech Stack:** Python 3.11+, argparse (no click/typer), PyYAML for frontmatter, stdlib `os.open(O_CREAT|O_EXCL)` for claim locks, atomic `.tmp`-swap writes, pytest + `tmp_path` + `monkeypatch` + subprocess, mypy strict. Reuses CCST's `roots.py`/`sessions.py` for location resolution, `shell_install.py`'s sentinel-block pattern, `hooks_install.py`'s merge + atomic-write helpers, and `cccs_hooks/telemetry.py` for non-fatal hook logging.

---

## Conventions every task must follow

- **Type hints** on every function signature and module constant. `from __future__ import annotations` at the top of every module. Run `mypy --strict` clean.
- **No personal paths** in committed code or tests. Use `Path.home()` at runtime; use fictional placeholders (`/home/alice`, `/example/repos/project`) in tests. Never `/home/chris`.
- **en-GB spelling** in prose/comments (organise, behaviour, colour) to match the repo.
- **Validation at the CLI boundary only** (argparse + a single validator); internals trust validated input. No re-validation inside lib functions.
- **No bare `except`**, no `except: pass`. Catch specific exceptions; in hooks degrade to empty `additionalContext` + telemetry log, never block the session.
- **Atomic state writes** via the generalised `.tmp`-swap helper (Task 2). State transitions (read/claim/archive) are never naive rewrites.
- **DRY:** one `deliver()` in `service.py`, two thin wrappers (the `ccmsg deliver` subcommand and the `messaging_deliver` hook). One serialiser, one validator.
- **Commit after every green test.** Commit commands are written into each task for the implementer.

> **Note for the implementer:** every `git commit` command below is for *you* to run while executing the plan. The plan author did not run them. Work on a feature branch created via `superpowers:using-git-worktrees` before you start Task 1.

---

## File Structure

**New library package — `src/cc_session_tools/lib/messaging/`** (one responsibility per module):

- `src/cc_session_tools/lib/messaging/__init__.py` — package marker; re-exports nothing (avoid barrel-pull-in).
- `src/cc_session_tools/lib/messaging/store.py` — store root resolution (env-overridable `CCST_MESSAGES_ROOT`), partition derivation from a cwd (reuses `roots.py`), lazy directory creation, sortable id generation, subject→slug, other-paths slug, path builders for inbox/archive/cursors.
- `src/cc_session_tools/lib/messaging/message.py` — `Message` dataclass + frontmatter parse/serialise (PyYAML) + atomic text write (`.tmp`-swap). Round-trip safe.
- `src/cc_session_tools/lib/messaging/addressing.py` — `SessionContext` dataclass + `targets(message, ctx)` deciding whether a message is for this session (session/project) or a description-candidate.
- `src/cc_session_tools/lib/messaging/cursor.py` — per-session high-water cursor read/write keyed on session uuid; `is_new(message, cursor)` + `advance(cursor, message)`.
- `src/cc_session_tools/lib/messaging/lock.py` — `O_CREAT|O_EXCL` sidecar claim lock as a context manager; `AlreadyClaimedError`.
- `src/cc_session_tools/lib/messaging/retention.py` — archive read/claimed messages older than 14 days into `archive/YYYY-MM/`; unread never archived.
- `src/cc_session_tools/lib/messaging/service.py` — the shared `deliver(ctx, mode)`, `send(...)`, `read_one(...)`, `list_messages(...)`, `claim(...)`, `archive(...)`; the digest formatter. The CLI and the hook both call into here.

**New CLI:**

- `src/cc_session_tools/cli/ccmsg.py` — thin argparse layer (`_build_parser()`, `main(argv=None) -> int`, `--version`) dispatching to `service.py`.

**New hook:**

- `src/cccs_hooks/messaging_deliver.py` — reads stdin JSON, builds a `SessionContext`, selects sweep mode from `hookEventName`, calls `service.deliver`, emits `additionalContext`. Never raises.

**New CLAUDE.md primitive:**

- `src/cc_session_tools/lib/claude_md_install.py` — sentinel-managed-block install/uninstall for `~/.claude/CLAUDE.md`, mirroring `shell_install.py`.

**New skill (doc-only, no tests):**

- `skills/send-session-message/SKILL.md`.

**Modified files:**

- `src/cc_session_tools/cli/ccst.py` — add `messaging-deliver` to `HOOK_VERBS`/`HOOK_DESCRIPTIONS`; add `claude-md install/uninstall` noun/verbs + dispatch.
- `config/hooks-bundle.json` — add `messaging-deliver` blocks to `SessionStart` and `UserPromptSubmit`.
- `pyproject.toml` — add `pyyaml` dep + `types-PyYAML` dev; add `ccmsg` script; bump version 0.12.0 → 0.13.0.
- `install-everything.sh` — insert `ccst claude-md install --apply` step, fix `N/total` counters.
- `README.md` — new "Inter-session messaging" section + skill/hook/`claude-md` table entries.
- `CHANGELOG.md` — `### Added` entries under `[Unreleased]`.
- `skills/move-session/SKILL.md`, `skills/move-session/scripts/move_session.py`, `skills/move-session/tests/` — tag-refresh in pending messages + cursor move on project move.

**New tests** (all under existing `tests/`, already in `testpaths`):

- `tests/messaging/test_store.py`, `test_message.py`, `test_addressing.py`, `test_cursor.py`, `test_lock.py`, `test_retention.py`, `test_service.py`, `test_ccmsg_cli.py`, `test_messaging_deliver_hook.py`, `tests/test_claude_md_install.py`.

---

# Phase A — core library

All Phase A modules live under `src/cc_session_tools/lib/messaging/`. Create the package marker first.

### Task 0 (setup): create the package marker and the test directory

**Files:**
- Create: `src/cc_session_tools/lib/messaging/__init__.py`
- Create: `tests/messaging/__init__.py` *(empty; lets pytest import the test package cleanly)*

- [ ] **Step 1: Create the package files**

```python
# src/cc_session_tools/lib/messaging/__init__.py
"""Inter-session messaging library: store, message format, addressing,
cursor, locking, retention, and the shared delivery service."""
```

```python
# tests/messaging/__init__.py  (empty file)
```

- [ ] **Step 2: Commit**

```bash
git add src/cc_session_tools/lib/messaging/__init__.py tests/messaging/__init__.py
git commit -m "chore: scaffold messaging package and test dir"
```

---

### Task 1: `store.py` — paths, partitions, ids, slugs

**Files:**
- Create: `src/cc_session_tools/lib/messaging/store.py`
- Test: `tests/messaging/test_store.py`

**Responsibilities:** resolve the store root (`CCST_MESSAGES_ROOT` env override → default `~/.claude/cc-messages`), derive a partition from a cwd (reusing `roots.py`), build inbox/archive/cursor paths, lazily create dirs, generate sortable ids, and slugify subjects/paths.

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_store.py
from __future__ import annotations

import re
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import store


def test_store_root_honours_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path / "msgs"))
    assert store.store_root() == tmp_path / "msgs"


def test_store_root_defaults_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CCST_MESSAGES_ROOT", raising=False)
    assert store.store_root() == Path.home() / ".claude" / "cc-messages"


def test_generate_id_is_sortable_and_unique() -> None:
    a = store.generate_id()
    b = store.generate_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{4}", a)
    assert a != b


def test_slug_subject_is_kebab_and_bounded() -> None:
    assert store.slug_subject("Hello, World! A very LONG subject line here") == "hello-world-a-very-long-subject"
    assert store.slug_subject("") == "untitled"


def test_other_paths_slug_is_stable_hash_plus_basename() -> None:
    s1 = store.other_paths_slug(Path("/example/weird path/My Project"))
    s2 = store.other_paths_slug(Path("/example/weird path/My Project"))
    assert s1 == s2
    assert re.fullmatch(r"[0-9a-f]{8}-my-project", s1)


def test_partition_for_strict_root_is_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj_root = tmp_path / "proj"
    (proj_root / "alpha").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(proj_root))
    assert store.partition_for_cwd(proj_root / "alpha") == "projects/alpha"


def test_partition_for_loose_root_is_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repos"
    (repo_root / "beta").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repo_root))
    assert store.partition_for_cwd(repo_root / "beta") == "repos/beta"


def test_partition_for_unknown_cwd_is_other_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    part = store.partition_for_cwd(tmp_path / "nowhere" / "thing")
    assert part.startswith("other-paths/")


def test_inbox_dir_is_created_lazily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    inbox = store.ensure_inbox_dir("projects/alpha")
    assert inbox == tmp_path / "projects" / "alpha" / "inbox"
    assert inbox.is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/messaging/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError` (store has no such functions).

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/store.py
"""Message store layout: root resolution, partition derivation, id and slug
generation, and lazy directory creation.

Store layout (under ``store_root()``)::

    <root>/projects/<name>/{inbox,archive/YYYY-MM}/
    <root>/repos/<name>/{inbox,archive/YYYY-MM}/
    <root>/other-paths/<slug>/{inbox,archive/YYYY-MM}/   # keyed on path slug
    <root>/_global/{inbox,archive/YYYY-MM}/              # description + broadcast
    <root>/.cursors/<session-uuid>.json

Partition strings are POSIX-style relative paths (``"projects/alpha"``) so they
are stable cursor keys and store-portable.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib.roots import (
    RootsConfigError,
    is_strict_root,
    load_session_roots,
    matched_session_root,
)

STORE_ROOT_ENV = "CCST_MESSAGES_ROOT"
GLOBAL_PARTITION = "_global"
CURSORS_DIRNAME = ".cursors"

_SLUG_MAX = 30
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def store_root() -> Path:
    """Resolve the message-store root. ``CCST_MESSAGES_ROOT`` overrides the
    default ``~/.claude/cc-messages`` (tests redirect via the env var)."""
    raw = os.environ.get(STORE_ROOT_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "cc-messages"


def generate_id() -> str:
    """A lexicographically-sortable id: ``YYYYMMDDTHHMMSSZ-<rand4>`` (UTC)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


def slug_subject(subject: str) -> str:
    """Kebab-case, lower, bounded slug of a subject for human file scanning."""
    cleaned = _SLUG_NON_ALNUM.sub("-", subject.lower()).strip("-")
    cleaned = cleaned[:_SLUG_MAX].strip("-")
    return cleaned or "untitled"


def other_paths_slug(abspath: Path) -> str:
    """Stable slug for a cwd outside all known roots: 8 hex of the path's
    SHA-1 plus a kebab basename, so two different paths never collide."""
    digest = hashlib.sha1(str(abspath).encode()).hexdigest()[:8]
    return f"{digest}-{slug_subject(abspath.name)}"


def partition_for_cwd(cwd: Path) -> str:
    """Map a session's cwd to its store partition.

    A cwd whose parent is the strict (PROJ) root → ``projects/<name>``; whose
    parent is a loose (REPO) root → ``repos/<name>``; anything else →
    ``other-paths/<slug>``. Reuses ``roots.py`` so project detection is not
    reinvented."""
    cwd = cwd.resolve() if cwd.exists() else cwd
    try:
        roots = load_session_roots()
    except RootsConfigError:
        roots = []
    matched = matched_session_root(cwd, roots) if roots else None
    if matched is not None:
        prefix = "projects" if is_strict_root(matched) else "repos"
        return f"{prefix}/{cwd.name}"
    return f"other-paths/{other_paths_slug(cwd)}"


def partition_dir(partition: str) -> Path:
    """Absolute directory for a partition string under the store root."""
    return store_root() / partition


def ensure_inbox_dir(partition: str) -> Path:
    inbox = partition_dir(partition) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def archive_dir(partition: str, when: datetime) -> Path:
    month = when.strftime("%Y-%m")
    d = partition_dir(partition) / "archive" / month
    d.mkdir(parents=True, exist_ok=True)
    return d


def cursors_dir() -> Path:
    d = store_root() / CURSORS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def message_filename(message_id: str, subject: str) -> str:
    """``<sortable-id>__<slug>.md`` — id is the routing key, slug is cosmetic."""
    return f"{message_id}__{slug_subject(subject)}.md"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/messaging/test_store.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/store.py tests/messaging/test_store.py
git commit -m "feat(messaging): store paths, partitions, ids, slugs"
```

---

### Task 2: `message.py` — `Message` dataclass, frontmatter round-trip, atomic write

**Files:**
- Create: `src/cc_session_tools/lib/messaging/message.py`
- Test: `tests/messaging/test_message.py`

**Responsibilities:** a typed `Message` (all frontmatter fields), `parse(text) -> Message`, `serialise(message) -> str`, `write_atomic(path, message)` (`.tmp`-swap), and a generalised `write_text_atomic(path, text)` reused by retention/claim. PyYAML for the frontmatter block.

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_message.py
from __future__ import annotations

from pathlib import Path

from cc_session_tools.lib.messaging.message import (
    Message,
    parse,
    serialise,
    write_atomic,
)


def _sample() -> Message:
    return Message(
        id="20260620T231500Z-a1b2",
        schema=1,
        from_project="oneshot",
        from_session="20260615-oneshot-inter-session-message-skill",
        from_uuid="8dbed047-0000-0000-0000-000000000000",
        to_kind="session",
        to_value="aaaa1111-2222-3333-4444-555566667777",
        to_location="projects/oneshot",
        subject="Short human subject",
        sent_at="2026-06-20T23:15:00Z",
        status="sent",
        read_at=None,
        read_by_uuid=None,
        read_by_session=None,
        claimed_at=None,
        receipt_shown=False,
        thread=None,
        attachments=["/abs/path/to/file.md"],
        body="Free-form markdown body.\nSecond line.\n",
    )


def test_round_trip_preserves_all_fields() -> None:
    m = _sample()
    assert parse(serialise(m)) == m


def test_serialise_emits_frontmatter_then_body() -> None:
    text = serialise(_sample())
    assert text.startswith("---\n")
    assert "\n---\n" in text
    assert text.rstrip().endswith("Second line.")


def test_parse_rejects_missing_frontmatter() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse("no frontmatter here\n")


def test_write_atomic_round_trips_via_disk(tmp_path: Path) -> None:
    path = tmp_path / "m.md"
    write_atomic(path, _sample())
    assert not (tmp_path / "m.md.tmp").exists()
    assert parse(path.read_text()) == _sample()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/messaging/test_message.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/message.py
"""Message file format: a typed dataclass, YAML-frontmatter round-trip, and
atomic writes. The frontmatter is the single source of truth for routing and
state; the body is free-form markdown."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml

ToKind = Literal["session", "project", "description"]
Status = Literal["sent", "read", "claimed", "archived"]

_FRONTMATTER_KEYS = (
    "id", "schema", "from_project", "from_session", "from_uuid",
    "to_kind", "to_value", "to_location", "subject", "sent_at",
    "status", "read_at", "read_by_uuid", "read_by_session",
    "claimed_at", "receipt_shown", "thread", "attachments",
)


@dataclass
class Message:
    id: str
    schema: int
    from_project: str
    from_session: str
    from_uuid: str
    to_kind: ToKind
    to_value: str
    to_location: str
    subject: str
    sent_at: str
    status: Status
    read_at: str | None
    read_by_uuid: str | None
    read_by_session: str | None
    claimed_at: str | None
    receipt_shown: bool
    thread: str | None
    attachments: list[str] = field(default_factory=list)
    body: str = ""


def serialise(message: Message) -> str:
    data = asdict(message)
    body = data.pop("body")
    front = {k: data[k] for k in _FRONTMATTER_KEYS}
    yaml_block = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
    return f"---\n{yaml_block}---\n\n{body}"


def parse(text: str) -> Message:
    if not text.startswith("---\n"):
        raise ValueError("message file has no YAML frontmatter")
    rest = text[len("---\n"):]
    end = rest.find("\n---\n")
    if end == -1:
        raise ValueError("message frontmatter is not terminated by '---'")
    yaml_block = rest[:end]
    body = rest[end + len("\n---\n"):].lstrip("\n")
    front = yaml.safe_load(yaml_block) or {}
    if not isinstance(front, dict):
        raise ValueError("message frontmatter is not a mapping")
    return Message(
        id=str(front["id"]),
        schema=int(front["schema"]),
        from_project=str(front["from_project"]),
        from_session=str(front["from_session"]),
        from_uuid=str(front["from_uuid"]),
        to_kind=front["to_kind"],
        to_value=str(front["to_value"]),
        to_location=str(front["to_location"]),
        subject=str(front["subject"]),
        sent_at=str(front["sent_at"]),
        status=front["status"],
        read_at=front["read_at"],
        read_by_uuid=front["read_by_uuid"],
        read_by_session=front["read_by_session"],
        claimed_at=front["claimed_at"],
        receipt_shown=bool(front["receipt_shown"]),
        thread=front["thread"],
        attachments=list(front.get("attachments") or []),
        body=body,
    )


def write_text_atomic(path: Path, text: str) -> None:
    """Generalised atomic text write (``.tmp``-swap), mirroring
    ``hooks_install.write_json_atomic``."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def write_atomic(path: Path, message: Message) -> None:
    write_text_atomic(path, serialise(message))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/messaging/test_message.py -v`
Expected: PASS.

> Note: the `.tmp` suffix is `m.md.tmp` (suffix appended), so the test asserting `m.md.tmp` does not exist after write is correct.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/message.py tests/messaging/test_message.py
git commit -m "feat(messaging): Message dataclass, frontmatter round-trip, atomic write"
```

---

### Task 3: `addressing.py` — `SessionContext` + `targets()`

**Files:**
- Create: `src/cc_session_tools/lib/messaging/addressing.py`
- Test: `tests/messaging/test_addressing.py`

**Responsibilities:** a `SessionContext` (uuid, project label, partition); `targets(message, ctx) -> MatchKind` deciding whether a message is for this session. Session-addressed matches on uuid; project-addressed matches on project label; description-addressed in `_global` is a candidate (advisory) for any session.

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_addressing.py
from __future__ import annotations

from cc_session_tools.lib.messaging.addressing import (
    MatchKind,
    SessionContext,
    targets,
)
from cc_session_tools.lib.messaging.message import Message


def _msg(**over: object) -> Message:
    base = dict(
        id="20260620T000000Z-0001", schema=1, from_project="x",
        from_session="x", from_uuid="sender", to_kind="session",
        to_value="me-uuid", to_location="projects/alpha",
        subject="s", sent_at="2026-06-20T00:00:00Z", status="sent",
        read_at=None, read_by_uuid=None, read_by_session=None,
        claimed_at=None, receipt_shown=False, thread=None,
        attachments=[], body="b",
    )
    base.update(over)
    return Message(**base)  # type: ignore[arg-type]


def _ctx() -> SessionContext:
    return SessionContext(uuid="me-uuid", project="alpha", partition="projects/alpha")


def test_session_addressed_to_my_uuid_matches() -> None:
    assert targets(_msg(to_kind="session", to_value="me-uuid"), _ctx()) is MatchKind.RECIPIENT


def test_session_addressed_to_other_uuid_no_match() -> None:
    assert targets(_msg(to_kind="session", to_value="other"), _ctx()) is MatchKind.NONE


def test_project_addressed_to_my_project_matches() -> None:
    assert targets(_msg(to_kind="project", to_value="alpha"), _ctx()) is MatchKind.RECIPIENT


def test_project_addressed_to_other_project_no_match() -> None:
    assert targets(_msg(to_kind="project", to_value="beta"), _ctx()) is MatchKind.NONE


def test_description_addressed_is_a_candidate() -> None:
    assert targets(_msg(to_kind="description", to_value="whoever does X"), _ctx()) is MatchKind.CANDIDATE


def test_already_read_message_does_not_match_recipient_again() -> None:
    m = _msg(to_kind="session", to_value="me-uuid", status="read")
    assert targets(m, _ctx()) is MatchKind.NONE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/messaging/test_addressing.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/addressing.py
"""Decide whether a message is addressed to a given session context.

Identity is always the session uuid (never the display tag). Project matching
uses the project label. Description-addressed messages are advisory candidates
that any session may claim."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from cc_session_tools.lib.messaging.message import Message


class MatchKind(str, Enum):
    NONE = "none"
    RECIPIENT = "recipient"     # auto-read applies
    CANDIDATE = "candidate"     # description-addressed; propose + claim


@dataclass(frozen=True)
class SessionContext:
    uuid: str
    project: str
    partition: str


def targets(message: Message, ctx: SessionContext) -> MatchKind:
    if message.status in ("read", "claimed", "archived"):
        return MatchKind.NONE
    if message.to_kind == "session":
        return MatchKind.RECIPIENT if message.to_value == ctx.uuid else MatchKind.NONE
    if message.to_kind == "project":
        return MatchKind.RECIPIENT if message.to_value == ctx.project else MatchKind.NONE
    return MatchKind.CANDIDATE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/messaging/test_addressing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/addressing.py tests/messaging/test_addressing.py
git commit -m "feat(messaging): addressing match (session/project/description)"
```

---

### Task 4: `cursor.py` — per-session high-water mark

**Files:**
- Create: `src/cc_session_tools/lib/messaging/cursor.py`
- Test: `tests/messaging/test_cursor.py`

**Responsibilities:** load/save `.cursors/<uuid>.json` (`{"high_water": {"<partition>": "<last-id>"}}`); `is_new(message, cursor)` (message id lexicographically > stored high-water for its partition); `advance(cursor, message)` (raise high-water). Keyed on session uuid so renames don't reset it.

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_cursor.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import cursor as cur
from cc_session_tools.lib.messaging.message import Message


def _msg(mid: str, location: str) -> Message:
    return Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location=location, subject="s",
        sent_at="2026-06-20T00:00:00Z", status="sent", read_at=None,
        read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    )


def test_empty_cursor_treats_everything_as_new() -> None:
    c = cur.Cursor.empty()
    assert cur.is_new(_msg("20260620T000000Z-0001", "projects/alpha"), c)


def test_advance_then_older_is_not_new() -> None:
    c = cur.Cursor.empty()
    newer = _msg("20260620T120000Z-0002", "projects/alpha")
    older = _msg("20260620T110000Z-0001", "projects/alpha")
    c = cur.advance(c, newer)
    assert not cur.is_new(newer, c)
    assert not cur.is_new(older, c)


def test_advance_is_per_partition() -> None:
    c = cur.Cursor.empty()
    c = cur.advance(c, _msg("20260620T120000Z-0002", "projects/alpha"))
    # A message in a different partition is unaffected by alpha's high-water.
    assert cur.is_new(_msg("20260620T010000Z-0001", "repos/beta"), c)


def test_save_and_load_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    c = cur.advance(cur.Cursor.empty(), _msg("20260620T120000Z-0002", "projects/alpha"))
    cur.save("uuid-123", c)
    assert cur.load("uuid-123") == c


def test_load_missing_cursor_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    assert cur.load("never-seen") == cur.Cursor.empty()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/messaging/test_cursor.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/cursor.py
"""Per-session delivery cursor. Keyed on the stable session uuid so a rename
never resets it. Stores a per-partition high-water id; a message is new to a
session iff its (sortable) id exceeds the stored high-water for its partition."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cc_session_tools.lib.messaging.message import Message
from cc_session_tools.lib.messaging.message import write_text_atomic
from cc_session_tools.lib.messaging.store import cursors_dir


@dataclass(frozen=True)
class Cursor:
    high_water: dict[str, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "Cursor":
        return cls(high_water={})


def is_new(message: Message, cursor: Cursor) -> bool:
    hw = cursor.high_water.get(message.to_location)
    return hw is None or message.id > hw


def advance(cursor: Cursor, message: Message) -> Cursor:
    hw = dict(cursor.high_water)
    current = hw.get(message.to_location)
    if current is None or message.id > current:
        hw[message.to_location] = message.id
    return Cursor(high_water=hw)


def _path(session_uuid: str) -> Path:
    return cursors_dir() / f"{session_uuid}.json"


def load(session_uuid: str) -> Cursor:
    path = _path(session_uuid)
    if not path.is_file():
        return Cursor.empty()
    data = json.loads(path.read_text())
    return Cursor(high_water=dict(data.get("high_water", {})))


def save(session_uuid: str, cursor: Cursor) -> None:
    write_text_atomic(
        _path(session_uuid),
        json.dumps({"high_water": cursor.high_water}, indent=2) + "\n",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/messaging/test_cursor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/cursor.py tests/messaging/test_cursor.py
git commit -m "feat(messaging): per-session high-water delivery cursor"
```

---

### Task 5: `lock.py` — `O_EXCL` claim lock with a race test

**Files:**
- Create: `src/cc_session_tools/lib/messaging/lock.py`
- Test: `tests/messaging/test_lock.py`

**Responsibilities:** `claim_lock(message_id)` context manager using `os.open(path, O_CREAT|O_EXCL|O_WRONLY)`; the winner holds the lock and removes it on exit; a contending caller gets `AlreadyClaimedError`. Includes a concurrency race test (threads) asserting exactly one winner.

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_lock.py
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging.lock import AlreadyClaimedError, claim_lock


def test_first_claim_succeeds_then_releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    with claim_lock("20260620T000000Z-0001"):
        pass
    # Lock released on exit, so a second claim also succeeds.
    with claim_lock("20260620T000000Z-0001"):
        pass


def test_second_concurrent_claim_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    with claim_lock("20260620T000000Z-0001"):
        with pytest.raises(AlreadyClaimedError):
            with claim_lock("20260620T000000Z-0001"):
                pass


def test_race_has_exactly_one_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    winners = 0
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        nonlocal winners
        barrier.wait()
        try:
            with claim_lock("race-id"):
                with lock:
                    winners += 1
                # Hold briefly so contenders overlap.
                import time
                time.sleep(0.02)
        except AlreadyClaimedError:
            return

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert winners == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/messaging/test_lock.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/lock.py
"""First-claim-wins lock for description-addressed messages.

Atomicity comes from ``os.open(O_CREAT | O_EXCL)``: exactly one caller creates
the sidecar lock file; everyone else sees ``FileExistsError`` and is told the
message is already claimed. Locks live under ``<store>/.locks/``."""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cc_session_tools.lib.messaging.store import store_root


class AlreadyClaimedError(Exception):
    """Raised when a lock for a message id is already held by another caller."""


def _locks_dir() -> Path:
    d = store_root() / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def claim_lock(message_id: str) -> Iterator[None]:
    lock_path = _locks_dir() / f"{message_id}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AlreadyClaimedError(message_id) from exc
    try:
        yield
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/messaging/test_lock.py -v`
Expected: PASS (including the 8-thread race with exactly one winner).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/lock.py tests/messaging/test_lock.py
git commit -m "feat(messaging): O_EXCL first-claim-wins lock with race test"
```

---

### Task 6: `retention.py` — archive read/claimed messages older than 14 days

**Files:**
- Create: `src/cc_session_tools/lib/messaging/retention.py`
- Test: `tests/messaging/test_retention.py`

**Responsibilities:** `archive_old(partition, now) -> list[str]` — for each inbox message whose `status` is `read`/`claimed` and whose `read_at`/`claimed_at` is > 14 days before `now`, set `status: archived` and **move** the file into `archive/YYYY-MM/`. Unread (`sent`) messages are never archived. Boundary test: 13 days stays, 15 days archives.

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_retention.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import retention, store
from cc_session_tools.lib.messaging.message import Message, parse, write_atomic


def _write(partition: str, mid: str, status: str, stamp: str | None) -> Path:
    inbox = store.ensure_inbox_dir(partition)
    msg = Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location=partition, subject="s",
        sent_at="2026-06-01T00:00:00Z", status=status,  # type: ignore[arg-type]
        read_at=stamp, read_by_uuid="r" if stamp else None,
        read_by_session="r" if stamp else None,
        claimed_at=None, receipt_shown=False, thread=None, attachments=[], body="b",
    )
    path = inbox / store.message_filename(mid, "s")
    write_atomic(path, msg)
    return path


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_unread_never_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    p = _write("projects/alpha", "20260101T000000Z-0001", "sent", None)
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    assert retention.archive_old("projects/alpha", now) == []
    assert p.exists()


def test_read_13_days_old_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    p = _write("projects/alpha", "20260101T000000Z-0001", "read", _iso(now - timedelta(days=13)))
    assert retention.archive_old("projects/alpha", now) == []
    assert p.exists()


def test_read_15_days_old_is_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    p = _write("projects/alpha", "20260101T000000Z-0001", "read", _iso(now - timedelta(days=15)))
    archived = retention.archive_old("projects/alpha", now)
    assert archived == ["20260101T000000Z-0001"]
    assert not p.exists()
    moved = list((tmp_path / "projects" / "alpha" / "archive").rglob("*.md"))
    assert len(moved) == 1
    assert parse(moved[0].read_text()).status == "archived"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/messaging/test_retention.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/retention.py
"""Opportunistic retention: archive read/claimed messages older than 14 days.

Archiving is a move (never a delete). Unread messages never expire. Called from
``deliver`` with a bounded per-sweep cost."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from cc_session_tools.lib.messaging.message import Message, parse, write_atomic
from cc_session_tools.lib.messaging.store import archive_dir, ensure_inbox_dir

_RETENTION_DAYS = 14
_ARCHIVABLE = ("read", "claimed")


def _parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _settled_at(message: Message) -> str | None:
    return message.claimed_at or message.read_at


def archive_old(partition: str, now: datetime) -> list[str]:
    """Archive eligible messages in ``partition``'s inbox. Returns the ids
    archived (sorted by encounter order)."""
    inbox = ensure_inbox_dir(partition)
    cutoff = now - timedelta(days=_RETENTION_DAYS)
    archived: list[str] = []
    for path in sorted(inbox.glob("*.md")):
        message = parse(path.read_text())
        if message.status not in _ARCHIVABLE:
            continue
        stamp = _settled_at(message)
        if stamp is None or _parse_stamp(stamp) > cutoff:
            continue
        message.status = "archived"
        dest = archive_dir(partition, _parse_stamp(stamp)) / path.name
        write_atomic(dest, message)
        path.unlink()
        archived.append(message.id)
    return archived
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/messaging/test_retention.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/retention.py tests/messaging/test_retention.py
git commit -m "feat(messaging): 14-day retention via archive-move"
```

---

# Phase B — `ccmsg` CLI + delivery service

### Task 7: scaffold `ccmsg` + `service.send` + `ccmsg send`

**Files:**
- Create: `src/cc_session_tools/lib/messaging/service.py`
- Create: `src/cc_session_tools/cli/ccmsg.py`
- Modify: `pyproject.toml` (add `pyyaml` dep, `types-PyYAML` dev, `ccmsg` script)
- Test: `tests/messaging/test_service.py`, `tests/messaging/test_ccmsg_cli.py`

**Responsibilities:** `service.send(...)` builds a `Message`, writes it to the partition's inbox, and returns the message id. The CLI validates *exactly one* recipient kind, non-empty subject+body, and absolute attachment paths at the boundary. Add the `pyyaml` runtime dep now (frontmatter is YAML; a hand-rolled parser would duplicate a solved problem and breach the secure-defaults rule).

- [ ] **Step 1: Write the failing tests**

```python
# tests/messaging/test_service.py  (send portion)
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import service, store
from cc_session_tools.lib.messaging.message import parse


def _sender() -> service.SendRequest:
    return service.SendRequest(
        from_project="oneshot",
        from_session="20260615-oneshot-x",
        from_uuid="sender-uuid",
        to_kind="project",
        to_value="alpha",
        to_partition="projects/alpha",
        subject="Hello there",
        body="Body text.",
        attachments=["/abs/a.md"],
        thread=None,
    )


def test_send_writes_message_to_partition_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(_sender())
    files = list((tmp_path / "projects" / "alpha" / "inbox").glob("*.md"))
    assert len(files) == 1
    m = parse(files[0].read_text())
    assert m.id == mid
    assert m.status == "sent"
    assert m.subject == "Hello there"
    assert m.attachments == ["/abs/a.md"]
```

```python
# tests/messaging/test_ccmsg_cli.py  (send portion)
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def _run(args: list[str], env_root: Path, extra_env: dict[str, str] | None = None):
    import os

    env = dict(os.environ)
    env["CCST_MESSAGES_ROOT"] = str(env_root)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", *args],
        capture_output=True, text=True, env=env,
    )


def test_send_happy_path(tmp_path: Path) -> None:
    res = _run(
        ["send", "--to-project", "alpha", "--subject", "Hi", "--body", "Body",
         "--from-project", "oneshot", "--from-session", "s", "--from-uuid", "u",
         "--from-partition", "projects/oneshot", "--to-partition", "projects/alpha"],
        tmp_path,
    )
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "projects" / "alpha" / "inbox").is_dir()


def test_send_rejects_no_recipient(tmp_path: Path) -> None:
    res = _run(["send", "--subject", "Hi", "--body", "B",
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/a"],
               tmp_path)
    assert res.returncode != 0
    assert "exactly one" in (res.stderr + res.stdout).lower()


def test_send_rejects_two_recipients(tmp_path: Path) -> None:
    res = _run(["send", "--to-project", "a", "--to-session", "u2",
                "--subject", "Hi", "--body", "B",
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/a"],
               tmp_path)
    assert res.returncode != 0


def test_send_rejects_empty_body(tmp_path: Path) -> None:
    res = _run(["send", "--to-project", "a", "--subject", "Hi", "--body", "",
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/a"],
               tmp_path)
    assert res.returncode != 0


def test_send_rejects_relative_attachment(tmp_path: Path) -> None:
    res = _run(["send", "--to-project", "a", "--subject", "Hi", "--body", "B",
                "--attach", "relative/path.md",
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/a"],
               tmp_path)
    assert res.returncode != 0
    assert "absolute" in (res.stderr + res.stdout).lower()
```

> **Design note:** the sender/partition context (`--from-*`, `--to-partition`) is normally supplied by the `send-session-message` skill from the live hook stdin. Exposing them as flags keeps the CLI testable in isolation (per the conftest convention) and matches the spec's "or flags for testing" note. The skill fills them in for real sends.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py -v`
Expected: FAIL (`ModuleNotFoundError` for `service`; `ccmsg` module not found).

- [ ] **Step 3: Write minimal implementation**

First, `service.py` (send only for this task; later tasks extend it):

```python
# src/cc_session_tools/lib/messaging/service.py
"""Shared messaging service used by both the ccmsg CLI and the delivery hook.

This module holds business logic; argparse validation stays in the CLI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from cc_session_tools.lib.messaging.message import Message, ToKind, write_atomic
from cc_session_tools.lib.messaging.store import (
    ensure_inbox_dir,
    generate_id,
    message_filename,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SendRequest:
    from_project: str
    from_session: str
    from_uuid: str
    to_kind: ToKind
    to_value: str
    to_partition: str
    subject: str
    body: str
    attachments: list[str] = field(default_factory=list)
    thread: str | None = None


def send(request: SendRequest) -> str:
    """Build and persist a message. Returns its id. Inputs are trusted (the
    CLI/schema validates them)."""
    message_id = generate_id()
    message = Message(
        id=message_id,
        schema=1,
        from_project=request.from_project,
        from_session=request.from_session,
        from_uuid=request.from_uuid,
        to_kind=request.to_kind,
        to_value=request.to_value,
        to_location=request.to_partition,
        subject=request.subject,
        sent_at=_now_iso(),
        status="sent",
        read_at=None,
        read_by_uuid=None,
        read_by_session=None,
        claimed_at=None,
        receipt_shown=False,
        thread=request.thread,
        attachments=list(request.attachments),
        body=request.body,
    )
    inbox = ensure_inbox_dir(request.to_partition)
    write_atomic(inbox / message_filename(message_id, request.subject), message)
    return message_id
```

Then the CLI scaffold + `send`:

```python
# src/cc_session_tools/cli/ccmsg.py
"""ccmsg — inter-session messaging CLI.

Thin argparse layer over cc_session_tools.lib.messaging.service. Validation
lives here at the boundary; the service trusts its inputs."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cc_session_tools import __version__
from cc_session_tools.lib.messaging import service


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccmsg",
        description="Send and read messages between Claude Code sessions.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    send_p = sub.add_parser("send", help="Compose and route a message.")
    rcpt = send_p.add_argument_group("recipient (exactly one)")
    rcpt.add_argument("--to-session", metavar="UUID")
    rcpt.add_argument("--to-project", metavar="NAME")
    rcpt.add_argument("--to-description", metavar="TEXT")
    send_p.add_argument("--subject", required=True)
    body = send_p.add_mutually_exclusive_group(required=True)
    body.add_argument("--body")
    body.add_argument("--body-file", type=Path)
    send_p.add_argument("--attach", action="append", default=[], metavar="PATH")
    send_p.add_argument("--thread", default=None, metavar="ID")
    # Sender + routing context (supplied by the skill from hook stdin; flags for tests).
    send_p.add_argument("--from-project", required=True)
    send_p.add_argument("--from-session", required=True)
    send_p.add_argument("--from-uuid", required=True)
    send_p.add_argument("--from-partition", required=True)
    send_p.add_argument("--to-partition", required=True,
                        help="Store partition the message file lives in.")
    return p


def _resolve_recipient(args: argparse.Namespace) -> tuple[str, str]:
    chosen = [
        ("session", args.to_session),
        ("project", args.to_project),
        ("description", args.to_description),
    ]
    set_ones = [(kind, val) for kind, val in chosen if val is not None]
    if len(set_ones) != 1:
        raise ValueError(
            "exactly one of --to-session / --to-project / --to-description is required"
        )
    return set_ones[0]


def _resolve_body(args: argparse.Namespace) -> str:
    body = args.body if args.body is not None else args.body_file.read_text()
    if not body.strip():
        raise ValueError("message body must not be empty")
    return body


def _validate_attachments(attachments: list[str]) -> None:
    for a in attachments:
        if not Path(a).is_absolute():
            raise ValueError(f"attachment path must be absolute: {a}")


def _cmd_send(args: argparse.Namespace) -> int:
    try:
        if not args.subject.strip():
            raise ValueError("subject must not be empty")
        to_kind, to_value = _resolve_recipient(args)
        body = _resolve_body(args)
        _validate_attachments(args.attach)
    except ValueError as exc:
        print(f"ccmsg: {exc}", file=sys.stderr)
        return 2
    message_id = service.send(service.SendRequest(
        from_project=args.from_project,
        from_session=args.from_session,
        from_uuid=args.from_uuid,
        to_kind=to_kind,  # type: ignore[arg-type]
        to_value=to_value,
        to_partition=args.to_partition,
        subject=args.subject,
        body=body,
        attachments=list(args.attach),
        thread=args.thread,
    ))
    print(message_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "send":
        return _cmd_send(args)
    _build_parser().print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

> **Note on the `to_kind` cast:** the value is one of the three string literals produced by `_resolve_recipient`, so it is in fact a `ToKind`. mypy cannot narrow the tuple element; the `# type: ignore[arg-type]` is the single sanctioned escape at this boundary. (Alternative if you prefer zero ignores: make `_resolve_recipient` return `tuple[ToKind, str]` by literal-typing each branch.)

Then `pyproject.toml` edits:

```toml
# [project] dependencies — add pyyaml
dependencies = [
    "pandas>=2.2",
    "pyarrow>=15.0",
    "jsonschema>=4.21",
    "platformdirs>=4.0",
    "httpx>=0.27",
    "pyyaml>=6",
]
```

```toml
# [project.scripts] — add ccmsg
ccmsg = "cc_session_tools.cli.ccmsg:main"
```

```toml
# [dependency-groups] dev — add the mypy stub
dev = [
    "mypy>=1.10",
    "pytest>=7",
    "pytest-mock>=3.12",
    "types-PyYAML",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pip install -e . && pytest tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py -v`
Expected: PASS. (Reinstall so the new `ccmsg` console-script / `pyyaml` are available; the subprocess tests invoke `python -m cc_session_tools.cli.ccmsg`, which only needs `pyyaml` importable.)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/cc_session_tools/lib/messaging/service.py src/cc_session_tools/cli/ccmsg.py tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py
git commit -m "feat(ccmsg): send subcommand + service.send + pyyaml dep"
```

---

### Task 8: `ccmsg read <id>` and `ccmsg list`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/service.py` (add `read_one`, `list_messages`, an id-locator)
- Modify: `src/cc_session_tools/cli/ccmsg.py` (add `read` and `list` subcommands)
- Test: `tests/messaging/test_service.py` (read/list portion), `tests/messaging/test_ccmsg_cli.py` (read/list portion)

**Responsibilities:** `find_by_id(message_id) -> Path | None` scans inbox+archive across all partitions; `read_one` returns the parsed `Message`; `list_messages(status=?, partition=?, from_uuid=?)` returns compact rows. CLI `read <id>` prints metadata + body; `list` prints one compact line per message; both report a structured error on a missing id.

- [ ] **Step 1: Write the failing tests**

```python
# tests/messaging/test_service.py  (read/list portion — append)
def test_read_one_returns_message(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging import service
    mid = service.send(_sender())
    msg = service.read_one(mid)
    assert msg is not None
    assert msg.subject == "Hello there"


def test_read_one_missing_id_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging import service
    assert service.read_one("nope") is None


def test_list_messages_filters_by_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging import service
    service.send(_sender())
    rows = service.list_messages(status="sent")
    assert len(rows) == 1
    assert service.list_messages(status="read") == []
```

```python
# tests/messaging/test_ccmsg_cli.py  (read/list portion — append)
def test_read_missing_id_errors(tmp_path):
    res = _run(["read", "does-not-exist"], tmp_path)
    assert res.returncode != 0
    assert "not found" in (res.stderr + res.stdout).lower()


def test_list_empty_store_ok(tmp_path):
    res = _run(["list"], tmp_path)
    assert res.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py -v -k "read or list"`
Expected: FAIL (`AttributeError: read_one` / unknown subcommand).

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/service.py  (add)
from dataclasses import dataclass as _dataclass  # reuse top import in practice
from pathlib import Path

from cc_session_tools.lib.messaging.message import Message, parse
from cc_session_tools.lib.messaging.store import store_root


def _iter_message_files() -> "list[Path]":
    root = store_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def find_by_id(message_id: str) -> Path | None:
    for path in _iter_message_files():
        if path.name.startswith(f"{message_id}__"):
            return path
    return None


def read_one(message_id: str) -> Message | None:
    path = find_by_id(message_id)
    return parse(path.read_text()) if path is not None else None


@dataclass(frozen=True)
class MessageRow:
    id: str
    status: str
    to_kind: str
    to_value: str
    from_session: str
    subject: str


def list_messages(
    *, status: str | None = None, partition: str | None = None,
    from_uuid: str | None = None,
) -> list[MessageRow]:
    rows: list[MessageRow] = []
    for path in _iter_message_files():
        m = parse(path.read_text())
        if status is not None and m.status != status:
            continue
        if partition is not None and m.to_location != partition:
            continue
        if from_uuid is not None and m.from_uuid != from_uuid:
            continue
        rows.append(MessageRow(
            id=m.id, status=m.status, to_kind=m.to_kind, to_value=m.to_value,
            from_session=m.from_session, subject=m.subject,
        ))
    return rows
```

```python
# src/cc_session_tools/cli/ccmsg.py  (add subparsers + dispatch)
# In _build_parser(), after the send parser:
    read_p = sub.add_parser("read", help="Print one message body and metadata.")
    read_p.add_argument("id")

    list_p = sub.add_parser("list", help="List messages (compact).")
    list_p.add_argument("--status", default=None)
    list_p.add_argument("--partition", default=None)
    list_p.add_argument("--from-uuid", default=None)

# New command handlers:
def _cmd_read(args: argparse.Namespace) -> int:
    message = service.read_one(args.id)
    if message is None:
        print(f"ccmsg: message not found: {args.id}", file=sys.stderr)
        return 1
    print(f"id:       {message.id}")
    print(f"from:     {message.from_session} ({message.from_project})")
    print(f"to:       {message.to_kind}={message.to_value}")
    print(f"subject:  {message.subject}")
    print(f"status:   {message.status}")
    print(f"sent_at:  {message.sent_at}")
    if message.attachments:
        print("attach:   " + ", ".join(message.attachments))
    print()
    print(message.body.rstrip())
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = service.list_messages(
        status=args.status, partition=args.partition, from_uuid=args.from_uuid,
    )
    for r in rows:
        print(f"[{r.id}] {r.status:8} {r.to_kind}={r.to_value} · {r.subject}")
    return 0

# In main(), extend dispatch:
    if args.command == "read":
        return _cmd_read(args)
    if args.command == "list":
        return _cmd_list(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/service.py src/cc_session_tools/cli/ccmsg.py tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py
git commit -m "feat(ccmsg): read and list subcommands"
```

---

### Task 9: `service.deliver()` + `ccmsg deliver`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/service.py` (add `deliver` + digest formatter + auto-read/receipt flips)
- Modify: `src/cc_session_tools/cli/ccmsg.py` (add `deliver` subcommand reading stdin JSON or flags)
- Test: `tests/messaging/test_service.py` (deliver portion)

**Responsibilities:** the single shared `deliver(ctx, mode)`:
1. Determine partitions to sweep: the session's own partition + `_global`. (`SessionStart` = full sweep; `UserPromptSubmit` = cursor-diffed incremental sweep — same code, the cursor naturally bounds it.)
2. For each message: if `targets()` is `RECIPIENT` and `is_new`, atomically flip `sent → read`, stamp `read_at`/`read_by_*`, advance cursor → add a digest line. If `CANDIDATE` and `is_new` and still `sent`, add a proposal line (no flip), advance cursor.
3. Receipts: any message *this session sent* now `read`/`claimed` with `receipt_shown == false` → emit a receipt line, flip `receipt_shown = true`.
4. Opportunistic retention: call `retention.archive_old` on each swept partition.
5. Return the assembled digest string (empty string if nothing).

Auto-read fires exactly once because the cursor advances past the message; receipts fire exactly once because `receipt_shown` flips.

- [ ] **Step 1: Write the failing tests**

```python
# tests/messaging/test_service.py  (deliver portion — append)
from cc_session_tools.lib.messaging.addressing import SessionContext


def _ctx(uuid="me-uuid", project="alpha", partition="projects/alpha"):
    return SessionContext(uuid=uuid, project=project, partition=partition)


def _send_to_session(monkeypatch, tmp_path, target_uuid):
    from cc_session_tools.lib.messaging import service
    return service.send(service.SendRequest(
        from_project="oneshot", from_session="s", from_uuid="sender",
        to_kind="session", to_value=target_uuid, to_partition="projects/alpha",
        subject="For you", body="Body.", attachments=[], thread=None,
    ))


def test_deliver_auto_reads_session_message_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging import service, cursor
    mid = _send_to_session(monkeypatch, tmp_path, "me-uuid")
    digest1 = service.deliver(_ctx(), mode="full")
    assert mid in digest1
    assert service.read_one(mid).status == "read"
    # Second sweep: cursor has advanced; the message is not re-surfaced.
    digest2 = service.deliver(_ctx(), mode="incremental")
    assert mid not in digest2


def test_deliver_does_not_auto_read_other_sessions_message(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging import service
    mid = _send_to_session(monkeypatch, tmp_path, "someone-else")
    digest = service.deliver(_ctx(), mode="full")
    assert mid not in digest
    assert service.read_one(mid).status == "sent"


def test_deliver_surfaces_receipt_once_to_sender(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging import service
    mid = _send_to_session(monkeypatch, tmp_path, "me-uuid")
    # Recipient reads it.
    service.deliver(_ctx(uuid="me-uuid"), mode="full")
    # Sender sweeps and sees a one-time receipt.
    sender_ctx = _ctx(uuid="sender", project="alpha", partition="projects/alpha")
    d1 = service.deliver(sender_ctx, mode="full")
    assert "read" in d1.lower() and mid in d1
    d2 = service.deliver(sender_ctx, mode="full")
    assert mid not in d2  # receipt_shown flipped


def test_deliver_surfaces_description_as_proposal_without_reading(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging import service
    mid = service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="description", to_value="whoever works on X",
        to_partition="_global", subject="task", body="b", attachments=[], thread=None,
    ))
    digest = service.deliver(_ctx(), mode="full")
    assert mid in digest
    assert service.read_one(mid).status == "sent"  # candidate, not read
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/messaging/test_service.py -v -k deliver`
Expected: FAIL (`AttributeError: deliver`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/service.py  (add)
from datetime import datetime, timezone

from cc_session_tools.lib.messaging import cursor as cursor_mod
from cc_session_tools.lib.messaging import retention
from cc_session_tools.lib.messaging.addressing import MatchKind, SessionContext, targets
from cc_session_tools.lib.messaging.message import write_atomic
from cc_session_tools.lib.messaging.store import GLOBAL_PARTITION, ensure_inbox_dir


def _relative_age(sent_at: str, now: datetime) -> str:
    sent = datetime.strptime(sent_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    minutes = int((now - sent).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _digest_line(message: Message, now: datetime) -> str:
    short = message.id.split("-")[-1]
    return (
        f"[{short}] from {message.from_session} ({message.from_project}) · "
        f"{message.subject} · {_relative_age(message.sent_at, now)}"
    )


def _swept_partitions(ctx: SessionContext) -> list[str]:
    parts = [ctx.partition]
    if GLOBAL_PARTITION not in parts:
        parts.append(GLOBAL_PARTITION)
    return parts


def deliver(ctx: SessionContext, *, mode: str) -> str:
    """Sweep relevant partitions, auto-read recipient messages, surface
    description proposals, emit receipts, run opportunistic retention, and
    return a compact digest (empty if nothing to show). ``mode`` is advisory
    (``full`` vs ``incremental``); the cursor bounds both identically."""
    now = datetime.now(timezone.utc)
    cur = cursor_mod.load(ctx.uuid)
    inbound: list[str] = []
    proposals: list[str] = []

    for partition in _swept_partitions(ctx):
        inbox = ensure_inbox_dir(partition)
        for path in sorted(inbox.glob("*.md")):
            message = parse(path.read_text())
            if not cursor_mod.is_new(message, cur):
                continue
            kind = targets(message, ctx)
            if kind is MatchKind.RECIPIENT:
                message.status = "read"
                message.read_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                message.read_by_uuid = ctx.uuid
                # SessionContext carries no display tag (the SessionStart/
                # UserPromptSubmit stdin does not include one), so auto-read
                # stamps the project label here. The sender's receipt will
                # therefore read "by <project>" for auto-read and "by
                # <session-tag>" for an explicit claim. This is intentional —
                # do NOT "fix" it by inventing a tag. If a true session tag is
                # ever wanted in receipts, add a `session_tag` field to
                # SessionContext threaded from the hook stdin.
                message.read_by_session = ctx.project
                write_atomic(path, message)
                inbound.append(_digest_line(message, now))
                cur = cursor_mod.advance(cur, message)
            elif kind is MatchKind.CANDIDATE:
                proposals.append(_digest_line(message, now))
                cur = cursor_mod.advance(cur, message)
        retention.archive_old(partition, now)

    cursor_mod.save(ctx.uuid, cur)
    receipts = _collect_receipts(ctx, now)

    return _format_digest(inbound, proposals, receipts)


def _collect_receipts(ctx: SessionContext, now: datetime) -> list[str]:
    lines: list[str] = []
    for path in _iter_message_files():
        message = parse(path.read_text())
        if message.from_uuid != ctx.uuid:
            continue
        if message.status not in ("read", "claimed"):
            continue
        if message.receipt_shown:
            continue
        short = message.id.split("-")[-1]
        who = message.read_by_session or "a session"
        lines.append(f'✓ read: "{message.subject}" by {who} ({_relative_age(message.sent_at, now)}) [{short}]')
        message.receipt_shown = True
        write_atomic(path, message)
    return lines


def _format_digest(inbound: list[str], proposals: list[str], receipts: list[str]) -> str:
    if not (inbound or proposals or receipts):
        return ""
    out: list[str] = ["[cc-messages] You have inter-session messages:"]
    out.extend(inbound)
    if proposals:
        out.append("Unclaimed messages addressed by description (claim if this session fits):")
        out.extend(proposals)
    if receipts:
        out.extend(receipts)
    out.append(
        "Read a body with `ccmsg read <id>`. To take a description-addressed "
        "message, confirm with the user then `ccmsg claim <id>`."
    )
    return "\n".join(out)
```

```python
# src/cc_session_tools/cli/ccmsg.py  (add deliver subcommand)
import json

# In _build_parser(), add:
    deliver_p = sub.add_parser("deliver", help="Sweep + digest (hook entry).")
    deliver_p.add_argument("--mode", choices=("full", "incremental"), default="full")
    deliver_p.add_argument("--uuid", default=None)
    deliver_p.add_argument("--project", default=None)
    deliver_p.add_argument("--partition", default=None)
    deliver_p.add_argument("--stdin", action="store_true",
                           help="Read session context from a hook JSON payload on stdin.")

# New handler:
def _cmd_deliver(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.messaging.addressing import SessionContext
    from cc_session_tools.lib.messaging import store

    if args.stdin:
        data = json.loads(sys.stdin.read())
        uuid = str(data.get("session_id", ""))
        cwd = Path(str(data.get("cwd", Path.cwd())))
        partition = store.partition_for_cwd(cwd)
        project = partition.split("/", 1)[-1]
    else:
        uuid = args.uuid or ""
        partition = args.partition or ""
        project = args.project or ""
    ctx = SessionContext(uuid=uuid, project=project, partition=partition)
    digest = service.deliver(ctx, mode=args.mode)
    if digest:
        print(digest)
    return 0

# In main(), extend dispatch:
    if args.command == "deliver":
        return _cmd_deliver(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/messaging/test_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/service.py src/cc_session_tools/cli/ccmsg.py tests/messaging/test_service.py
git commit -m "feat(ccmsg): shared deliver() — auto-read, proposals, receipts, retention"
```

---

### Task 10: `ccmsg claim <id>` + `ccmsg archive <id>`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/service.py` (add `claim`, `archive`)
- Modify: `src/cc_session_tools/cli/ccmsg.py` (add `claim`, `archive` subcommands)
- Test: `tests/messaging/test_service.py` (claim/archive portion), `tests/messaging/test_ccmsg_cli.py`

**Responsibilities:** `claim(message_id, claimer)` takes the `O_EXCL` lock, flips `status: claimed` + stamps `claimed_at`/`read_by_*`, and returns the message; a second claim raises `AlreadyClaimedError` (CLI maps to a clear exit). `archive(message_id, now)` moves the file into `archive/YYYY-MM/` and flips `status: archived`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/messaging/test_service.py  (claim/archive portion — append)
def test_claim_flips_status_and_blocks_second_claimer(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging import service
    from cc_session_tools.lib.messaging.lock import AlreadyClaimedError
    mid = service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="description", to_value="X", to_partition="_global",
        subject="task", body="b", attachments=[], thread=None,
    ))
    claimer = service.Claimer(uuid="me", session="alpha")
    msg = service.claim(mid, claimer)
    assert msg.status == "claimed"
    assert msg.read_by_uuid == "me"
    import pytest
    with pytest.raises(AlreadyClaimedError):
        service.claim(mid, service.Claimer(uuid="other", session="beta"))


def test_archive_moves_message(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from datetime import datetime, timezone
    from cc_session_tools.lib.messaging import service
    mid = _send_to_session(monkeypatch, tmp_path, "me-uuid")
    service.archive(mid, datetime(2026, 6, 20, tzinfo=timezone.utc))
    assert service.read_one(mid).status == "archived"
    assert list((tmp_path / "projects" / "alpha" / "archive").rglob("*.md"))
```

```python
# tests/messaging/test_ccmsg_cli.py  (append)
def test_claim_missing_id_errors(tmp_path):
    res = _run(["claim", "nope", "--uuid", "u", "--session", "s"], tmp_path)
    assert res.returncode != 0


def test_archive_missing_id_errors(tmp_path):
    res = _run(["archive", "nope"], tmp_path)
    assert res.returncode != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py -v -k "claim or archive"`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/service.py  (add)
from cc_session_tools.lib.messaging.lock import AlreadyClaimedError, claim_lock
from cc_session_tools.lib.messaging.retention import _parse_stamp  # archive reuse
from cc_session_tools.lib.messaging.store import archive_dir


@dataclass(frozen=True)
class Claimer:
    uuid: str
    session: str


class MessageNotFoundError(Exception):
    """Raised when a message id resolves to no file."""


def claim(message_id: str, claimer: Claimer) -> Message:
    path = find_by_id(message_id)
    if path is None:
        raise MessageNotFoundError(message_id)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with claim_lock(message_id):
        message = parse(path.read_text())
        message.status = "claimed"
        message.claimed_at = now
        message.read_at = message.read_at or now
        message.read_by_uuid = claimer.uuid
        message.read_by_session = claimer.session
        write_atomic(path, message)
        return message


def archive(message_id: str, now: datetime) -> Message:
    path = find_by_id(message_id)
    if path is None:
        raise MessageNotFoundError(message_id)
    message = parse(path.read_text())
    message.status = "archived"
    dest = archive_dir(message.to_location, now) / path.name
    write_atomic(dest, message)
    if dest != path:
        path.unlink()
    return message
```

```python
# src/cc_session_tools/cli/ccmsg.py  (add claim/archive subcommands)
# In _build_parser():
    claim_p = sub.add_parser("claim", help="Claim a description-addressed message.")
    claim_p.add_argument("id")
    claim_p.add_argument("--uuid", required=True)
    claim_p.add_argument("--session", required=True)

    archive_p = sub.add_parser("archive", help="Manually archive a message.")
    archive_p.add_argument("id")

# Handlers:
def _cmd_claim(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.messaging.lock import AlreadyClaimedError
    from cc_session_tools.lib.messaging.service import (
        Claimer, MessageNotFoundError, claim,
    )
    try:
        message = claim(args.id, Claimer(uuid=args.uuid, session=args.session))
    except MessageNotFoundError:
        print(f"ccmsg: message not found: {args.id}", file=sys.stderr)
        return 1
    except AlreadyClaimedError:
        print(f"ccmsg: already claimed: {args.id}", file=sys.stderr)
        return 3
    print(f"claimed {message.id}")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone
    from cc_session_tools.lib.messaging.service import MessageNotFoundError, archive
    try:
        archive(args.id, datetime.now(timezone.utc))
    except MessageNotFoundError:
        print(f"ccmsg: message not found: {args.id}", file=sys.stderr)
        return 1
    print(f"archived {args.id}")
    return 0

# In main():
    if args.command == "claim":
        return _cmd_claim(args)
    if args.command == "archive":
        return _cmd_archive(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/service.py src/cc_session_tools/cli/ccmsg.py tests/messaging/test_service.py tests/messaging/test_ccmsg_cli.py
git commit -m "feat(ccmsg): claim (first-wins) and archive subcommands"
```

---

# Phase C — delivery hooks

### Task 11: `messaging_deliver` hook + `HOOK_VERBS` + bundle entries

**Files:**
- Create: `src/cccs_hooks/messaging_deliver.py`
- Modify: `src/cc_session_tools/cli/ccst.py:37-56` (add to `HOOK_VERBS` + `HOOK_DESCRIPTIONS`)
- Modify: `config/hooks-bundle.json` (add to `SessionStart` and `UserPromptSubmit`)
- Test: `tests/messaging/test_messaging_deliver_hook.py`

**Responsibilities:** the hook reads stdin JSON, builds a `SessionContext` (uuid from `session_id`, partition from `cwd`), picks the sweep mode from `hookEventName` (`SessionStart` → `full`; else → `incremental`), calls `service.deliver`, and emits `additionalContext`. It must **never raise**: on any failure it emits empty `additionalContext` and logs via `telemetry.log_event` (using the existing `TelemetryEntry` schema — `decision="annotate"`, `cache="none"`, `verdict="deliver-failed"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_messaging_deliver_hook.py
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cccs_hooks import messaging_deliver
from cc_session_tools.lib.messaging import service


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _capture_emit(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(messaging_deliver, "_emit", lambda ctx, event: out.append(ctx))
    return out


def test_hook_emits_digest_for_addressed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    # Send to the session uuid we will present as the recipient.
    cwd = tmp_path / "work"
    cwd.mkdir()
    from cc_session_tools.lib.messaging import store
    partition = store.partition_for_cwd(cwd)
    service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="session", to_value="recipient-uuid", to_partition=partition,
        subject="Ping", body="b", attachments=[], thread=None,
    ))
    _stdin(monkeypatch, {"hookEventName": "SessionStart", "session_id": "recipient-uuid", "cwd": str(cwd)})
    emitted = _capture_emit(monkeypatch)
    rc = messaging_deliver.main()
    assert rc == 0
    assert any("Ping" in e for e in emitted)


def test_hook_emits_empty_on_bad_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    emitted = _capture_emit(monkeypatch)
    rc = messaging_deliver.main()
    assert rc == 0
    assert emitted == [""]  # degrades to empty context, never blocks


def test_hook_does_not_resurface_after_first_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    cwd = tmp_path / "work"
    cwd.mkdir()
    from cc_session_tools.lib.messaging import store
    partition = store.partition_for_cwd(cwd)
    service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="session", to_value="recipient-uuid", to_partition=partition,
        subject="Once", body="b", attachments=[], thread=None,
    ))
    payload = {"hookEventName": "UserPromptSubmit", "session_id": "recipient-uuid", "cwd": str(cwd)}
    _stdin(monkeypatch, payload)
    out1 = _capture_emit(monkeypatch)
    messaging_deliver.main()
    assert any("Once" in e for e in out1)
    _stdin(monkeypatch, payload)
    out2 = _capture_emit(monkeypatch)
    messaging_deliver.main()
    assert not any("Once" in e for e in out2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/messaging/test_messaging_deliver_hook.py -v`
Expected: FAIL (`ModuleNotFoundError: cccs_hooks.messaging_deliver`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/cccs_hooks/messaging_deliver.py
"""SessionStart / UserPromptSubmit hook: deliver inter-session messages.

Builds a session context from the hook stdin payload, runs the shared
``service.deliver`` sweep, and injects a compact digest as additionalContext.
Never blocks a session: any failure degrades to empty additionalContext and is
logged via the CCST telemetry channel."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cc_session_tools.lib.messaging import service, store
from cc_session_tools.lib.messaging.addressing import SessionContext


def _emit(context: str, event: str) -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}},
        sys.stdout,
    )


def _log_failure(reason: str) -> None:
    # telemetry.log_event swallows its own I/O errors internally and never
    # raises, so no wrapper here (a swallow-only try/except is banned by the
    # repo's coding standards).
    from cccs_hooks.telemetry import TelemetryEntry, log_event
    log_event(TelemetryEntry(
        hook="messaging-deliver", event="", tool="", session_id="",
        cwd_short="", decision="annotate", cache="none",
        verdict=f"deliver-failed:{reason}", input_hash="",
    ))


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _log_failure("bad-stdin")
        _emit("", "UserPromptSubmit")
        return 0

    event = str(data.get("hookEventName", "UserPromptSubmit"))
    mode = "full" if event == "SessionStart" else "incremental"
    try:
        uuid = str(data.get("session_id", ""))
        cwd = Path(str(data.get("cwd", Path.cwd())))
        partition = store.partition_for_cwd(cwd)
        project = partition.split("/", 1)[-1]
        ctx = SessionContext(uuid=uuid, project=project, partition=partition)
        digest = service.deliver(ctx, mode=mode)
    except (OSError, ValueError) as exc:
        _log_failure(type(exc).__name__)
        _emit("", event)
        return 0

    _emit(digest, event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```python
# src/cc_session_tools/cli/ccst.py — add to HOOK_VERBS (after last-screenshot)
    "messaging-deliver": "cccs_hooks.messaging_deliver",
```

```python
# src/cc_session_tools/cli/ccst.py — add to HOOK_DESCRIPTIONS
    "messaging-deliver": "Delivers inter-session messages (digest + auto-read + receipts) on session start and each prompt",
```

```json
// config/hooks-bundle.json — add to SessionStart.hooks[] (append a hook entry)
{
  "type": "command",
  "command": "ccst hooks run messaging-deliver",
  "timeout": 10,
  "statusMessage": "Delivering inter-session messages..."
}
```

```json
// config/hooks-bundle.json — add to the existing UserPromptSubmit block.hooks[] (append)
{
  "type": "command",
  "command": "ccst hooks run messaging-deliver",
  "timeout": 10,
  "statusMessage": "Checking for new inter-session messages..."
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/messaging/test_messaging_deliver_hook.py -v`
Expected: PASS.

- [ ] **Step 5: Verify the bundle is valid JSON and round-trips through the installer dry-run**

Run: `python -c "import json,pathlib; json.loads(pathlib.Path('config/hooks-bundle.json').read_text()); print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add src/cccs_hooks/messaging_deliver.py src/cc_session_tools/cli/ccst.py config/hooks-bundle.json tests/messaging/test_messaging_deliver_hook.py
git commit -m "feat(hooks): messaging-deliver SessionStart + UserPromptSubmit hooks"
```

---

# Phase D — `ccst claude-md`

### Task 12: `claude_md_install.py` + `ccst claude-md install/uninstall`

**Files:**
- Create: `src/cc_session_tools/lib/claude_md_install.py`
- Modify: `src/cc_session_tools/cli/ccst.py` (add `claude-md` noun, `install`/`uninstall` verbs, dispatch)
- Test: `tests/test_claude_md_install.py`

**Responsibilities:** a sentinel-managed block in a target CLAUDE.md (default `~/.claude/CLAUDE.md`), mirroring `shell_install.py`: `install_claude_md(path, apply)` and `uninstall_claude_md(path, apply)` returning a result with an action enum; idempotent in-place replace; dry-run default. The managed block holds minimal proactive-messaging instructions. HTML-comment markers match the spec.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_md_install.py
from __future__ import annotations

from pathlib import Path

from cc_session_tools.lib.claude_md_install import (
    MarkdownAction,
    install_claude_md,
    uninstall_claude_md,
    _SENTINEL_START,
    _SENTINEL_END,
)


def test_install_adds_block(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# My instructions\n")
    result = install_claude_md(md, apply=True)
    assert result.action is MarkdownAction.ADDED
    text = md.read_text()
    assert _SENTINEL_START in text and _SENTINEL_END in text
    assert text.startswith("# My instructions")


def test_reinstall_is_idempotent(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    before = md.read_text()
    result = install_claude_md(md, apply=True)
    assert result.action is MarkdownAction.ALREADY_PRESENT
    assert md.read_text() == before


def test_uninstall_removes_block(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    result = uninstall_claude_md(md, apply=True)
    assert result.action is MarkdownAction.REMOVED
    assert _SENTINEL_START not in md.read_text()


def test_install_dry_run_does_not_write(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=False)
    assert _SENTINEL_START not in md.read_text()


def test_install_creates_missing_file(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    result = install_claude_md(md, apply=True)
    assert result.action is MarkdownAction.ADDED
    assert md.is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_claude_md_install.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/claude_md_install.py
"""Manage a sentinel-delimited block of proactive inter-session-messaging
instructions in the global ~/.claude/CLAUDE.md.

Mirrors shell_install.py: idempotent in-place replace between HTML-comment
markers, dry-run by default, atomic write on apply. Unlike shell_install (which
skips missing rc files), this creates a missing CLAUDE.md so first-time install
works."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from cc_session_tools.lib.messaging.message import write_text_atomic

_SENTINEL_START = "<!-- CCST:messaging START -->"
_SENTINEL_END = "<!-- CCST:messaging END -->"

_BLOCK = f"""\
{_SENTINEL_START}
## Inter-session messaging

You can leave a durable message for another Claude Code session (a specific
session, a whole project, or "whoever is working on X"). Use this proactively
when you discover something relevant to another project, hand off a sub-task, or
need to coordinate with another session - do not wait to be asked.

- When a cross-session message is warranted, use the `send-session-message` skill.
  It helps you choose the recipient (session / project / description), confirm an
  ambiguous recipient with the user, and call `ccmsg send`.
- Delivered messages arrive automatically as injected context. Read a body with
  `ccmsg read <id>`. For a description-addressed proposal, confirm with the user,
  then `ccmsg claim <id>` (first claim wins).
{_SENTINEL_END}
"""


class MarkdownAction(str, Enum):
    ADDED = "added"
    REPLACED = "replaced"
    REMOVED = "removed"
    ALREADY_PRESENT = "already-present"
    NOT_PRESENT = "not-present"


@dataclass(frozen=True)
class MarkdownResult:
    path: Path
    action: MarkdownAction
    message: str


def _find_block(lines: list[str]) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n").rstrip()
        if stripped == _SENTINEL_START:
            start = i
        elif stripped == _SENTINEL_END and start is not None:
            return (start, i)
    return None


def install_claude_md(path: Path, *, apply: bool = False) -> MarkdownResult:
    content = path.read_text() if path.exists() else ""
    lines = content.splitlines(keepends=True)
    span = _find_block(lines)

    if span is not None:
        start, end = span
        existing = "".join(lines[start : end + 1])
        if existing.rstrip("\n") == _BLOCK.rstrip("\n"):
            return MarkdownResult(path, MarkdownAction.ALREADY_PRESENT, "block already up to date")
        new_content = "".join(lines[:start] + [_BLOCK] + lines[end + 1 :])
        if apply:
            write_text_atomic(path, new_content)
        return MarkdownResult(
            path, MarkdownAction.REPLACED,
            f"{'replaced' if apply else 'would replace'} existing block",
        )

    sep = "" if content.endswith("\n") or not content else "\n"
    new_content = content + sep + _BLOCK
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, new_content)
    return MarkdownResult(
        path, MarkdownAction.ADDED, f"{'added' if apply else 'would add'} block",
    )


def uninstall_claude_md(path: Path, *, apply: bool = False) -> MarkdownResult:
    if not path.exists():
        return MarkdownResult(path, MarkdownAction.NOT_PRESENT, "file does not exist")
    content = path.read_text()
    lines = content.splitlines(keepends=True)
    span = _find_block(lines)
    if span is None:
        return MarkdownResult(path, MarkdownAction.NOT_PRESENT, "block not found")
    start, end = span
    new_content = "".join(lines[:start] + lines[end + 1 :])
    if apply:
        write_text_atomic(path, new_content)
    return MarkdownResult(
        path, MarkdownAction.REMOVED, f"{'removed' if apply else 'would remove'} block",
    )
```

```python
# src/cc_session_tools/cli/ccst.py — command handlers
def _cmd_claude_md_install(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.claude_md_install import install_claude_md
    target = Path(args.target) if args.target else (Path.home() / ".claude" / "CLAUDE.md")
    result = install_claude_md(target, apply=args.apply)
    print(f"  {result.path}: {result.message}")
    if not args.apply:
        print("\nDry run — re-run with --apply to write changes")
    return 0


def _cmd_claude_md_uninstall(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.claude_md_install import uninstall_claude_md
    target = Path(args.target) if args.target else (Path.home() / ".claude" / "CLAUDE.md")
    result = uninstall_claude_md(target, apply=args.apply)
    print(f"  {result.path}: {result.message}")
    if not args.apply:
        print("\nDry run — re-run with --apply to write changes")
    return 0
```

```python
# src/cc_session_tools/cli/ccst.py — in _build_parser(), add the noun
    cmd_parser = sub.add_parser("claude-md", help="Manage the global CLAUDE.md messaging block")
    cmd_sub = cmd_parser.add_subparsers(dest="verb", metavar="<verb>")
    cmd_sub.required = True
    cmd_install = cmd_sub.add_parser("install", help="Add/update the messaging block (dry run by default)")
    cmd_install.add_argument("--target", default=None, metavar="PATH",
                             help="CLAUDE.md path (default: ~/.claude/CLAUDE.md)")
    cmd_install.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    cmd_uninstall = cmd_sub.add_parser("uninstall", help="Remove the messaging block (dry run by default)")
    cmd_uninstall.add_argument("--target", default=None, metavar="PATH",
                               help="CLAUDE.md path (default: ~/.claude/CLAUDE.md)")
    cmd_uninstall.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
```

```python
# src/cc_session_tools/cli/ccst.py — in main(), add dispatch
    if args.noun == "claude-md":
        if args.verb == "install":
            sys.exit(_cmd_claude_md_install(args))
        if args.verb == "uninstall":
            sys.exit(_cmd_claude_md_uninstall(args))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_claude_md_install.py -v`
Expected: PASS.

- [ ] **Step 5: Smoke-test the CLI wiring on a tmp file**

Run: `python -m cc_session_tools.cli.ccst claude-md install --target /tmp/ccst-cm-test.md --apply && grep -c "CCST:messaging" /tmp/ccst-cm-test.md && rm /tmp/ccst-cm-test.md`
Expected: prints the result line, then `2`.

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/claude_md_install.py src/cc_session_tools/cli/ccst.py tests/test_claude_md_install.py
git commit -m "feat(ccst): claude-md install/uninstall managed block"
```

---

# Phase E — skill + move-session

### Task 13: `send-session-message` skill (doc-only)

**Files:**
- Create: `skills/send-session-message/SKILL.md`

**Responsibilities:** a doc-only skill (auto-discovered by `ccst skills install` because it has a `SKILL.md`; no testpaths change). It guides Claude to recognise when to send, choose addressing, confirm an ambiguous recipient, compose to the writing-style rules, call `ccmsg send`, and handle description-proposals (`ccmsg claim`).

- [ ] **Step 1: Create the skill file**

```markdown
---
name: send-session-message
description: Use when you want to leave a durable message for another Claude Code session - a specific session, a whole project, or "whoever is working on X". Triggers on "tell the other session", "leave a note for the X project", "hand this off to whoever is doing Y", noticing something relevant to a different project, or coordinating two sessions without the user relaying by hand. Also use when a delivered description-addressed proposal arrives and this session may be the right place to claim it.
---

# Send a session message

`ccmsg` is the only sanctioned way to send a cross-session message. Compose with
care: the message is durable and auditable.

## When to send (proactively)

- You discover something relevant to a different project while working here.
- You are handing a sub-task to a session better placed to do it.
- Two sessions in the same project need to coordinate.

Do not send for things the user can see in this session, or to talk to yourself.

## Choose the recipient kind

Decide which of three addressing modes fits, and **confirm with the user when it
is ambiguous**:

1. `--to-session <uuid>` - a specific known session (you have its uuid).
2. `--to-project <name>` - any session in a named project.
3. `--to-description "<text>"` - "whoever is working on X". Surfaced to candidate
   sessions; one claims it.

If you are unsure which the user means, ask before sending.

## Compose

- Apply the user's writing-style rules: state the ask first, one point per
  message, cut filler.
- Attach by absolute path only (`--attach /abs/path`). The store references
  files; it does not copy them.

## Send

The delivery hook supplies your own session context. When you call `ccmsg send`,
pass the routing context the skill resolves from the current session (uuid,
project, partition). Subject and body are required; exactly one recipient kind
is required.

## Receiving a description-addressed proposal

When a delivered digest shows an unclaimed description-addressed message and this
session is the right place to handle it:

1. Propose to the user: summarise the message and ask whether to claim it.
2. On confirmation, run `ccmsg claim <id>`. First claim wins; if another session
   claimed it first you will be told, and you do nothing.

Read any message body with `ccmsg read <id>`.
```

- [ ] **Step 2: Verify the skill is discoverable (dry run)**

Run: `python -m cc_session_tools.cli.ccst skills install --source skills --target /tmp/ccst-skills-test 2>&1 | grep send-session-message && rm -rf /tmp/ccst-skills-test`
Expected: the `send-session-message` row appears with action `create`.

- [ ] **Step 3: Commit**

```bash
git add skills/send-session-message/SKILL.md
git commit -m "feat(skills): send-session-message skill"
```

---

### Task 14: `move-session` — refresh display tags + move cursor on project move

**Files:**
- Modify: `skills/move-session/scripts/move_session.py` (add a messaging-safety step)
- Modify: `skills/move-session/SKILL.md` (document the new step)
- Test: `skills/move-session/tests/test_messaging_safety.py` (new)

**Responsibilities:** after a move/rename, (a) refresh `from_session` / `read_by_session` display tags in any pending (non-archived) message that references the moved session's uuid, and (b) on a *project move*, move the session's cursor file and re-evaluate the partition for future deliveries. uuid routing already works, so this is cosmetic + cursor relocation; no message is orphaned.

> **Read first:** `skills/move-session/scripts/move_session.py` to find where the move/rename completes and the session uuid + new tag are known. Insert the new step there, guarded so it is a no-op when the message store does not exist (most sessions have no messages).

- [ ] **Step 1: Write the failing test**

```python
# skills/move-session/tests/test_messaging_safety.py
from __future__ import annotations

from pathlib import Path

import pytest

# The messaging-safety helper lives in the messaging lib so move_session can
# import it; this test exercises it directly.
from cc_session_tools.lib.messaging.move_safety import (
    refresh_display_tags,
    relocate_cursor,
)
from cc_session_tools.lib.messaging import service, store, cursor


def test_refresh_display_tags_updates_pending_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(service.SendRequest(
        from_project="oneshot", from_session="old-tag", from_uuid="moved-uuid",
        to_kind="project", to_value="alpha", to_partition="projects/alpha",
        subject="s", body="b", attachments=[], thread=None,
    ))
    refresh_display_tags(uuid="moved-uuid", new_tag="new-tag")
    assert service.read_one(mid).from_session == "new-tag"


def test_relocate_cursor_moves_high_water(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    c = cursor.advance(cursor.Cursor.empty(), service.read_one(
        service.send(service.SendRequest(
            from_project="o", from_session="t", from_uuid="u",
            to_kind="project", to_value="alpha", to_partition="projects/alpha",
            subject="s", body="b", attachments=[], thread=None,
        ))
    ))
    cursor.save("moved-uuid", c)
    relocate_cursor(uuid="moved-uuid", old_partition="projects/old", new_partition="projects/new")
    loaded = cursor.load("moved-uuid")
    # The cursor still exists and is keyed on the same uuid (uuid-keyed survives moves).
    assert loaded == c
```

> The cursor is uuid-keyed, so a project move does not require renaming the cursor file; `relocate_cursor` is a defensive no-op that exists so the move-session flow has an explicit, tested call site (and a home for any future per-partition rekeying). Keep it minimal.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest skills/move-session/tests/test_messaging_safety.py -v`
Expected: FAIL (`ModuleNotFoundError: ...move_safety`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/messaging/move_safety.py
"""Rename/move safety for the message store, called by the move-session skill.

uuid routing means no message is ever orphaned by a rename; these helpers keep
the cosmetic display tag fresh and give the move flow an explicit cursor hook."""
from __future__ import annotations

from cc_session_tools.lib.messaging.message import parse, write_atomic
from cc_session_tools.lib.messaging.service import _iter_message_files
from cc_session_tools.lib.messaging import cursor as cursor_mod


def refresh_display_tags(*, uuid: str, new_tag: str) -> int:
    """Update from_session / read_by_session display tags for pending messages
    referencing ``uuid``. Returns the count updated."""
    updated = 0
    for path in _iter_message_files():
        message = parse(path.read_text())
        if message.status == "archived":
            continue
        changed = False
        if message.from_uuid == uuid and message.from_session != new_tag:
            message.from_session = new_tag
            changed = True
        if message.read_by_uuid == uuid and message.read_by_session != new_tag:
            message.read_by_session = new_tag
            changed = True
        if changed:
            write_atomic(path, message)
            updated += 1
    return updated


def relocate_cursor(*, uuid: str, old_partition: str, new_partition: str) -> None:
    """The cursor is uuid-keyed, so it survives a project move unchanged. This
    explicit call site exists for the move-session flow and future rekeying."""
    _ = (old_partition, new_partition)  # currently a no-op by design
    cursor_mod.save(uuid, cursor_mod.load(uuid))
```

Then wire it into `move_session.py` at the point the move/rename completes (after the new tag + uuid are known). Add, near the end of the successful-move path:

```python
# skills/move-session/scripts/move_session.py  (inside the apply/--execute branch,
# after the jsonl + cc-sessions move succeeds and you know session_uuid + dst_tag)
    try:
        from cc_session_tools.lib.messaging.move_safety import (
            refresh_display_tags, relocate_cursor,
        )
        if session_uuid:
            refresh_display_tags(uuid=session_uuid, new_tag=dst_tag)
            if cwd_changed:
                relocate_cursor(
                    uuid=session_uuid,
                    old_partition="",  # source partition not needed (uuid-keyed)
                    new_partition="",
                )
    except ImportError:
        pass  # messaging lib not installed; nothing to refresh
```

> **Implementer:** match the surrounding code's variable names (`session_uuid`, `dst_tag`, `cwd_changed`) - read the file and adjust to whatever the move path actually calls them. The only `except` here is `ImportError` (messaging not installed), which is a genuine optional-dependency boundary, not a swallowed error.

- [ ] **Step 4: Add the SKILL.md note**

Add a short subsection to `skills/move-session/SKILL.md` (under the existing safety/behaviour section) documenting:
- After a rename, display tags in pending messages are refreshed (uuid routing is unaffected).
- After a project move, the uuid-keyed cursor is preserved so no message is re-delivered or lost.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest skills/move-session/tests/test_messaging_safety.py skills/move-session/tests/ -v`
Expected: PASS (new tests pass; existing move-session tests still pass).

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/messaging/move_safety.py skills/move-session/scripts/move_session.py skills/move-session/SKILL.md skills/move-session/tests/test_messaging_safety.py
git commit -m "feat(move-session): refresh message display tags + preserve cursor on move"
```

---

# Phase F — integration, docs, version

### Task 15: installer, README, CHANGELOG, version bump, full-suite verification

**Files:**
- Modify: `install-everything.sh`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml` (version 0.12.0 → 0.13.0)

This task is docs/config; it ends with a full `mypy` + `pytest` run as the verification gate.

- [ ] **Step 1: Add the `claude-md install` step to the installer**

In `install-everything.sh`, renumber the step banners to `N/6` and insert a new step **before** the health check:

```bash
# ── Step 1: Install / upgrade CLIs ──────────
step "1/6  CLIs"
# (unchanged body)

# ── Step 2: Skills ──────────
step "2/6  Skills"
ccst skills install --apply

# ── Step 3: Hooks ──────────
step "3/6  Hooks"
ccst hooks install --apply

# ── Step 4: Shell helpers (ccl) ──────────
step "4/6  Shell helpers"
ccst shell install --apply

# ── Step 5: Global CLAUDE.md messaging block ──────────
step "5/6  Global CLAUDE.md"
ccst claude-md install --apply

# ── Step 6: Health check ──────────
step "6/6  Health check"
ccst doctor
```

- [ ] **Step 2: Verify the installer parses**

Run: `bash -n install-everything.sh && echo ok`
Expected: `ok`

- [ ] **Step 3: Add the README section + table entries**

Add a new top-level section `## Inter-session messaging` describing the store, `ccmsg` subcommands (table: send / deliver / read / list / claim / archive), and the no-prompt delivery model (SessionStart full sweep + UserPromptSubmit incremental sweep). Add:
- a "Bundled skills" entry for `send-session-message`,
- a "Hook library" note for `messaging-deliver` (both events),
- a "Hook management CLI (ccst)" entry for `claude-md install/uninstall`.

Keep the prose to the repo's existing style; do not restate the spec.

- [ ] **Step 4: Add the CHANGELOG entries**

Under `## [Unreleased]`, add an `### Added` block:

```markdown
### Added

- **Inter-session messaging.** A new `ccmsg` CLI sends durable, addressed,
  auditable messages between Claude Code sessions (to a session, a project, or a
  free-text description), stored as markdown-with-frontmatter under
  `~/.claude/cc-messages/`. Subcommands: `send`, `deliver`, `read`, `list`,
  `claim`, `archive`.
- **Automatic delivery hooks.** A `messaging-deliver` hook fires on `SessionStart`
  (full sweep) and `UserPromptSubmit` (incremental sweep), injecting a compact
  digest as additional context. Auto-read, read-receipts, first-claim-wins claims,
  and 14-day archival are all handled without prompting.
- **`send-session-message` skill** guiding recipient choice, confirmation, and
  composition.
- **`ccst claude-md install/uninstall`** maintains a managed proactive-messaging
  block in the global `~/.claude/CLAUDE.md`.
- **`move-session`** now refreshes message display tags and preserves the
  uuid-keyed delivery cursor across renames and project moves.
```

- [ ] **Step 5: Bump the version**

```toml
# pyproject.toml
version = "0.13.0"
```

- [ ] **Step 6: Run the full verification suite**

Run: `pip install -e . && mypy --strict src/cc_session_tools/lib/messaging src/cc_session_tools/cli/ccmsg.py src/cccs_hooks/messaging_deliver.py src/cc_session_tools/lib/claude_md_install.py`
Expected: `Success: no issues found`.

Run: `pytest`
Expected: all tests pass (existing + new). Investigate and fix any failure before proceeding — do not disable checks.

- [ ] **Step 7: Commit**

```bash
git add install-everything.sh README.md CHANGELOG.md pyproject.toml
git commit -m "docs+chore: messaging installer step, README, CHANGELOG, bump to 0.13.0"
```

---

## Final review

After Task 15, dispatch a `plan-document-reviewer` (or run `superpowers:requesting-code-review`) and run `ccst doctor` against a tmp settings/CLAUDE.md to confirm the new hook + claude-md block register cleanly. Then use `superpowers:finishing-a-development-branch` to decide on merge/PR.

## Notes for the implementer (gotchas)

- **`mypy --strict`** will flag the `to_kind` tuple element in `ccmsg.py`; the single sanctioned `# type: ignore[arg-type]` is documented in Task 7. Prefer the literal-typed-branch alternative if you want zero ignores.
- **Reinstall (`pip install -e .`)** after Task 7 so `pyyaml` is importable and the `ccmsg` console script exists; the subprocess CLI tests only need `pyyaml` importable, but the entry-point smoke tests need the reinstall.
- **Never touch real `~/.claude/`** in tests — every test sets `CCST_MESSAGES_ROOT` (and clears the roots env vars where partition derivation matters). The autouse conftest fixture already clears the roots vars; set them explicitly when a test needs them.
- **`move_session.py` variable names** — read the file; the messaging-safety insertion must match the actual local names at the completion point.
