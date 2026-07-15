# Phase 2: `ccmsg` → `ccmsg.db` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Read `2026-07-13-data-store-uplift-00-overview.md` and `2026-07-13-data-store-uplift-01-shared-infra.md` first — they fix the env-var conventions, the `lib/db.connect()` / `lib/paths.data_home()` contract this plan consumes, and the migration-safety rules (overview §4). Do not re-decide anything settled there.

**Goal:** move the inter-session messaging store off its flat-file/YAML-frontmatter + `.cursors/*.json` + `.locks/*.lock` layout onto a single WAL-mode `ccmsg.db`, closing the retention-vs-claim double-unlink crash (R1) at the SQL layer, without changing one byte of the `ccmsg` CLI contract.

**Architecture:** a new `messaging/repository.py` becomes the single home of all SQL (schema, row↔`Message` mapping, every query and every `BEGIN IMMEDIATE` mutation). `service.py`, `retention.py`, `cursor.py`, and `move_safety.py` are rewritten to delegate to it; `store.py` keeps only path/partition/id derivation; `message.py` keeps only `Message` + `parse`/`serialise` (used by the migration reader). `addressing.py`, the file-based `claim_lock` (`lock.py`, kept per R4), the `ccmsg` CLI, and the delivery hook are unchanged in behaviour. Atomicity that used to come from atomic-file-swap + an `os.O_EXCL` lock now comes from single-statement conditional `UPDATE`s inside `BEGIN IMMEDIATE` transactions, serialised by SQLite's single-writer WAL discipline.

**Tech Stack:** Python 3.11 stdlib (`sqlite3` with `RETURNING`, requires SQLite ≥ 3.35 — already enforced by `lib/db.connect()`), `json` for the `attachments` column, pytest + `monkeypatch`, real-subprocess CLI tests.

---

## Prerequisites

- Phase 1 is merged: `cc_session_tools.lib.db.connect/checkpoint/backup_to` and `cc_session_tools.lib.paths.data_home` exist and are tested. This plan imports both.
- Work on the `f/claude-data-store-uplift` branch (already synced with `main` in Phase 1). Confirm before starting:

```bash
git status
uv run python -c "from cc_session_tools.lib import db, paths; print(paths.data_home)"
```

Expected: `lib.db`/`lib.paths` import cleanly; working tree is Phase-1-clean.

---

## Fixed contract — preserve this interface exactly

Every one of these behaviours has an existing test and/or a live hook/skill depending on it. Only the storage backend changes; argument sets, stdout/stderr text, and exit codes below must be byte-identical after this phase.

- **`send`** — recipient flags `--to-session` / `--to-project` / `--to-description` (exactly one), `--subject` (required, non-empty), `--body` / `--body-file` (mutually exclusive, required, non-empty after strip), repeatable `--attach` (must be absolute), `--thread`, sender overrides `--from-*` / `--to-partition`. Sender auto-derivation: uuid from `$CLAUDE_CODE_SESSION_ID`, tag from `$CLD_SESSION_TAG`, project/partition from cwd. Prints the message id + exit 0. Validation errors print `ccmsg: <msg>` to **stderr**, exit **2**.
- **`read <id>`** — fixed-field dump: `id:` / `from:` / `to:` / `subject:` / `status:` / `sent_at:`, optional `attach:`, blank line, then `body.rstrip()`. Not found → `ccmsg: message not found: <id>` (stderr), exit **1**. Corrupt/unreadable → `ccmsg: message <id> is unreadable: <exc>` (stderr), exit **1**.
- **`list [--status] [--partition] [--from-uuid]`** — one line per message: `[<id>] <status:8> <to_kind>=<to_value> · <subject>`. Exit **0** always (empty store prints nothing, exit 0).
- **`deliver [--mode full|incremental] [--uuid] [--project] [--partition] [--stdin]`** — prints the digest if non-empty, exit **0** always.
- **`claim <id> --uuid --session`** — `claimed <id>` + exit **0**. Not found → `ccmsg: message not found: <id>`, exit **1**. Already claimed → `ccmsg: already claimed: <id>`, exit **3**.
- **`archive <id>`** — `archived <id>` + exit **0**. Not found → exit **1**. Lock-contended → `ccmsg: message is being claimed, try again: <id>`, exit **3**.

No `ccmsg query` subcommand is added: `list` and `read` already are ccmsg's read surface, and the fixed contract forbids adding flags. (The overview's "ship a query subcommand" convention is satisfied by the pre-existing `list`/`read`.)

---

## Concurrency requirements (become schema/transaction constraints)

- **R1 — retention must not race itself or a concurrent claim [HIGH].** Today `retention.archive_old()` does `path.unlink()` with no guard: two concurrent sweeps double-unlink and the second raises an uncaught `FileNotFoundError` that crashes `ccmsg deliver` (the CLI has no try/except around `service.deliver`; only the *hook* catches it). Fix: archiving becomes one atomic `UPDATE messages SET status='archived' WHERE to_location=? AND status IN ('read','claimed') AND … RETURNING id` inside `BEGIN IMMEDIATE`. A second concurrent sweep's identical UPDATE matches 0 rows — no crash, no double-work — and because it is a status flip (never a row delete/move), a claim that lands first keeps its `claimed_at`/`read_by_uuid`. Task 7 is a dedicated race test at `test_lock.py`-grade rigor.
- **R2 — auto-read attribution is deterministic (first-writer-wins) [LOW].** Auto-read is `UPDATE … SET status='read', read_by_uuid=? WHERE id=? AND status='sent'`. Two matching processes serialise under WAL; the first flips `sent→read` and stamps its uuid, the second matches 0 rows and stamps nothing. First-writer-wins, confirmed by a test.
- **R3 — id/filename collisions remain a non-issue [informational].** `generate_id()` (`YYYYMMDDTHHMMSSZ-<4 hex>`) is kept; it is the `messages` primary key, so a duplicate id is a PK conflict, not silent corruption. No behavioural change.
- **R4 — claim-lock orphan-on-crash gap [accepted, no work].** The file-based `claim_lock` (`os.open(O_CREAT|O_EXCL)`) is **kept as-is, outside the DB**. It is not folded into a SQL lock. A hard kill mid-claim still orphans `.locks/<id>.lock` until removed by hand; this is rare (claims are short) and explicitly accepted. `service.claim`/`service.archive` keep wrapping their SQL mutation in `with claim_lock(id):` — the SQL conditional UPDATE is what provides correctness (R1/R2); the file lock is the retained coarse envelope the existing tests exercise.

---

## File Structure

- Create: `src/cc_session_tools/lib/messaging/repository.py` — all SQL: schema, connect wrapper, row↔`Message`, every query + `BEGIN IMMEDIATE` mutation, cursor table, domain exceptions.
- Modify: `src/cc_session_tools/lib/messaging/store.py` — `store_root()` default → `paths.data_home()`; add `db_path()`; drop dead filesystem-layout helpers.
- Modify: `src/cc_session_tools/lib/messaging/message.py` — keep `Message`/`parse`/`serialise`; drop `write_text_atomic`/`write_atomic`/`safe_parse`.
- Modify: `src/cc_session_tools/lib/messaging/cursor.py` — `load`/`save` → repository cursor table; `Cursor`/`is_new`/`advance` unchanged.
- Modify: `src/cc_session_tools/lib/messaging/retention.py` — `archive_old` → `repository.archive_aged`.
- Modify: `src/cc_session_tools/lib/messaging/service.py` — every store touch → repository; `deliver`/`_collect_receipts` use indexed queries; keep `claim_lock` in `claim`/`archive`.
- Modify: `src/cc_session_tools/lib/messaging/move_safety.py` — `refresh_display_tags` → repository; `relocate_cursor` → repository cursor touch.
- Modify (exception-guard widening only — added after adversarial review, see Task 15):
  `src/cc_session_tools/cli/ccmsg.py`, `src/cccs_hooks/messaging_deliver.py`. Under the SQLite
  backend, a lock-contended writer raises `sqlite3.OperationalError` (a subclass of
  `sqlite3.Error`, not `OSError`/`ValueError`) once `busy_timeout` expires — the existing
  `except (OSError, ValueError)` guards in both files do not catch this, so a busy-DB collision
  would propagate uncaught out of the hook (breaking its "never blocks a session" invariant,
  documented at `messaging_deliver.py`'s module docstring line 5) and out of the CLI's `read`
  handler. Both guards must widen to `except (OSError, ValueError, sqlite3.Error)`.
- Unchanged: `addressing.py`, `lock.py`.
- Create: `scripts/migrate_ccmsg_to_db.py` — one-shot flat-tree → `ccmsg.db` migration (write→verify→tar-backup→delete).
- Tests touched: `tests/messaging/test_store.py`, `test_message.py`, `test_cursor.py`, `test_retention.py`, `test_service.py`, `test_ccmsg_cli.py`, `test_messaging_deliver_hook.py`; new `tests/messaging/test_repository.py`, `tests/messaging/test_repository_race.py`, `tests/test_migrate_ccmsg_to_db.py`.

**Migration-script placement decision (justified):** a standalone `scripts/migrate_ccmsg_to_db.py`, *not* a `ccst migrate ccmsg` subcommand. This mirrors the exact existing precedent `scripts/migrate_csv_to_db.py` (a one-shot, destructive, run-once-per-machine store migration living under `scripts/`). Keeping a genuinely destructive one-shot out of the everyday `ccst` surface reduces footgun risk; the `ccst tags migrate` subcommand exists because that migration is re-runnable and non-destructive, which this is not.

---

### Task 1: `store.py` — data-home root + `db_path()`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/store.py`
- Test: `tests/messaging/test_store.py`

- [ ] **Step 1: Update the failing tests**

Replace the default-root test and add a `db_path` test in `tests/messaging/test_store.py`:

```python
def test_store_root_defaults_to_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CCST_MESSAGES_ROOT", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "dh"))
    assert store.store_root() == tmp_path / "dh"


def test_db_path_is_ccmsg_db_under_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    assert store.db_path() == tmp_path / "ccmsg.db"
```

Delete `test_store_root_defaults_to_home` (asserted the retired `~/.claude/cc-messages` default) and `test_inbox_dir_is_created_lazily` (the helper it tests is removed in Task 8).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/messaging/test_store.py -v`
Expected: FAIL — `store.db_path` missing; `store_root()` still returns the `~/.claude` default.

- [ ] **Step 3: Update the implementation**

In `store.py`: add the import and change `store_root()`, add `db_path()`:

```python
from cc_session_tools.lib import paths

DB_FILENAME = "ccmsg.db"


def store_root() -> Path:
    """Directory holding ``ccmsg.db``. ``CCST_MESSAGES_ROOT`` overrides the
    default ``paths.data_home()`` (tests redirect via the env var)."""
    raw = os.environ.get(STORE_ROOT_ENV)
    if raw:
        return Path(raw).expanduser()
    return paths.data_home()


def db_path() -> Path:
    return store_root() / DB_FILENAME
```

Leave every other helper in `store.py` untouched for now (later tasks still import them; they are removed in Task 8). Update the module docstring's "Store layout" block to describe `<root>/ccmsg.db` instead of the partition tree.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/messaging/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/store.py tests/messaging/test_store.py
git commit -m "feat(ccmsg): resolve store root to data_home() and add db_path()"
```

---

### Task 2: `repository.py` — schema + connect

**Files:**
- Create: `src/cc_session_tools/lib/messaging/repository.py`
- Test: `tests/messaging/test_repository.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/messaging/test_repository.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import repository as repo
from cc_session_tools.lib.messaging import store


def test_connect_creates_ccmsg_db_with_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    conn = repo.connect()
    try:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert {"messages", "cursors"} <= names
    assert mode.lower() == "wal"
    assert (tmp_path / "ccmsg.db").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/messaging/test_repository.py -v`
Expected: FAIL — `No module named 'cc_session_tools.lib.messaging.repository'`.

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/messaging/repository.py
"""SQLite data-access layer for the inter-session message store (ccmsg.db).

The single home of all SQL. Every mutation runs inside a BEGIN IMMEDIATE
transaction so concurrent writers serialise under WAL: this is what closes the
old retention-vs-claim double-unlink race (R1) and makes auto-read attribution
first-writer-wins (R2) without any file-based coordination. Rows map 1:1 to the
Message dataclass; the body lives in a TEXT column (attachments stay as
absolute-path references, JSON-encoded, never embedded)."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from cc_session_tools.lib import db
from cc_session_tools.lib.messaging import store
from cc_session_tools.lib.messaging.message import Message

_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    "schema"        INTEGER NOT NULL,
    from_project    TEXT NOT NULL,
    from_session    TEXT NOT NULL,
    from_uuid       TEXT NOT NULL,
    to_kind         TEXT NOT NULL CHECK (to_kind IN ('session','project','description')),
    to_value        TEXT NOT NULL,
    to_location     TEXT NOT NULL,
    subject         TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('sent','read','claimed','archived')),
    read_at         TEXT,
    read_by_uuid    TEXT,
    read_by_session TEXT,
    claimed_at      TEXT,
    receipt_shown   INTEGER NOT NULL DEFAULT 0 CHECK (receipt_shown IN (0,1)),
    thread          TEXT,
    attachments     TEXT NOT NULL DEFAULT '[]',
    body            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_sweep    ON messages(to_location, id);
CREATE INDEX IF NOT EXISTS idx_messages_status   ON messages(to_location, status);
CREATE INDEX IF NOT EXISTS idx_messages_receipts ON messages(from_uuid, receipt_shown);

CREATE TABLE IF NOT EXISTS cursors (
    session_uuid          TEXT NOT NULL,
    partition             TEXT NOT NULL,
    high_water_message_id TEXT NOT NULL,
    PRIMARY KEY (session_uuid, partition)
);
"""


class MessageNotFoundError(Exception):
    """Raised when a message id resolves to no row."""


def connect() -> sqlite3.Connection:
    """Open ccmsg.db through the shared helper, in explicit-transaction mode.

    isolation_level=None turns off sqlite3's implicit BEGIN so every mutation
    can issue its own BEGIN IMMEDIATE (see _immediate)."""
    conn = db.connect(store.db_path(), ddl=_DDL)
    conn.isolation_level = None
    return conn


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the body inside a BEGIN IMMEDIATE / COMMIT, rolling back on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        schema=row["schema"],
        from_project=row["from_project"],
        from_session=row["from_session"],
        from_uuid=row["from_uuid"],
        to_kind=row["to_kind"],
        to_value=row["to_value"],
        to_location=row["to_location"],
        subject=row["subject"],
        sent_at=row["sent_at"],
        status=row["status"],
        read_at=row["read_at"],
        read_by_uuid=row["read_by_uuid"],
        read_by_session=row["read_by_session"],
        claimed_at=row["claimed_at"],
        receipt_shown=bool(row["receipt_shown"]),
        thread=row["thread"],
        attachments=list(json.loads(row["attachments"])),
        body=row["body"],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/messaging/test_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/repository.py tests/messaging/test_repository.py
git commit -m "feat(ccmsg): add repository.py schema, connect, row mapping"
```

---

### Task 3: repository — `insert`, `get_by_id`, `list_rows`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/repository.py`
- Test: `tests/messaging/test_repository.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/messaging/test_repository.py
from cc_session_tools.lib.messaging.message import Message


def _msg(mid: str, **over) -> Message:
    base = dict(
        id=mid, schema=1, from_project="oneshot", from_session="20260615-x",
        from_uuid="sender-uuid", to_kind="project", to_value="alpha",
        to_location="projects/alpha", subject="Hello there",
        sent_at="2026-06-20T00:00:00Z", status="sent", read_at=None,
        read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=["/abs/a.md"], body="Body.",
    )
    base.update(over)
    return Message(**base)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    return tmp_path


def test_insert_then_get_round_trips_all_fields(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001"))
    got = repo.get_by_id("20260620T000000Z-0001")
    assert got is not None
    assert got.subject == "Hello there"
    assert got.attachments == ["/abs/a.md"]
    assert got.receipt_shown is False
    assert got.read_at is None


def test_get_missing_returns_none(root: Path) -> None:
    assert repo.get_by_id("nope") is None


def test_list_rows_filters(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", from_uuid="u1", to_location="projects/alpha"))
    repo.insert(_msg("20260620T000000Z-0002", from_uuid="u2", to_location="projects/beta",
                     to_value="beta", status="read", read_at="2026-06-20T01:00:00Z"))
    assert len(repo.list_rows()) == 2
    assert len(repo.list_rows(status="sent")) == 1
    assert len(repo.list_rows(partition="projects/beta")) == 1
    assert len(repo.list_rows(from_uuid="u1")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/messaging/test_repository.py -v`
Expected: FAIL — `repository.insert` missing.

- [ ] **Step 3: Write the implementation**

Append to `repository.py`:

```python
_COLUMNS = (
    'id', '"schema"', 'from_project', 'from_session', 'from_uuid', 'to_kind',
    'to_value', 'to_location', 'subject', 'sent_at', 'status', 'read_at',
    'read_by_uuid', 'read_by_session', 'claimed_at', 'receipt_shown', 'thread',
    'attachments', 'body',
)


def _insert_params(message: Message) -> tuple[object, ...]:
    return (
        message.id, message.schema, message.from_project, message.from_session,
        message.from_uuid, message.to_kind, message.to_value, message.to_location,
        message.subject, message.sent_at, message.status, message.read_at,
        message.read_by_uuid, message.read_by_session, message.claimed_at,
        int(message.receipt_shown), message.thread,
        json.dumps(list(message.attachments)), message.body,
    )


def insert(message: Message) -> None:
    placeholders = ", ".join("?" for _ in _COLUMNS)
    sql = f"INSERT INTO messages ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
    conn = connect()
    try:
        with _immediate(conn):
            conn.execute(sql, _insert_params(message))
    finally:
        conn.close()


def get_by_id(message_id: str) -> Message | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row) if row is not None else None


def list_rows(
    *,
    status: str | None = None,
    partition: str | None = None,
    from_uuid: str | None = None,
) -> list[Message]:
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("status=?")
        params.append(status)
    if partition is not None:
        clauses.append("to_location=?")
        params.append(partition)
    if from_uuid is not None:
        clauses.append("from_uuid=?")
        params.append(from_uuid)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM messages{where} ORDER BY id", params
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/messaging/test_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/repository.py tests/messaging/test_repository.py
git commit -m "feat(ccmsg): repository insert/get_by_id/list_rows"
```

---

### Task 4: repository — `sweep_new` + `mark_read` (R2 first-writer-wins)

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/repository.py`
- Test: `tests/messaging/test_repository.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/messaging/test_repository.py

def test_sweep_new_respects_high_water(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", to_location="projects/alpha"))
    repo.insert(_msg("20260620T120000Z-0002", to_location="projects/alpha"))
    repo.insert(_msg("20260620T000000Z-0003", to_location="_global", to_value="x",
                     to_kind="description"))
    swept = repo.sweep_new(["projects/alpha", "_global"],
                           {"projects/alpha": "20260620T000000Z-0001"})
    ids = [m.id for m in swept]
    assert ids == ["20260620T120000Z-0002", "20260620T000000Z-0003"]


def test_mark_read_is_first_writer_wins(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", to_kind="project"))
    assert repo.mark_read("20260620T000000Z-0001", "uuid-A", "2026-06-20T02:00:00Z", "projA") is True
    # Second attempt: row is already 'read', so it stamps nothing and returns False.
    assert repo.mark_read("20260620T000000Z-0001", "uuid-B", "2026-06-20T03:00:00Z", "projB") is False
    got = repo.get_by_id("20260620T000000Z-0001")
    assert got is not None
    assert got.status == "read"
    assert got.read_by_uuid == "uuid-A"   # first writer won
    assert got.read_by_session == "projA"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/messaging/test_repository.py::test_mark_read_is_first_writer_wins -v`
Expected: FAIL — `repository.sweep_new` / `mark_read` missing.

- [ ] **Step 3: Write the implementation**

Append to `repository.py`:

```python
def sweep_new(partitions: list[str], high_water: dict[str, str]) -> list[Message]:
    """All messages in ``partitions`` newer (by id) than the caller's per-
    partition high-water, ordered by (partition, id). Terminal-status filtering
    is left to addressing.targets so cursor advancement matches the pre-SQL
    behaviour exactly."""
    if not partitions:
        return []
    placeholders = ", ".join("?" for _ in partitions)
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM messages WHERE to_location IN ({placeholders}) "
            "ORDER BY to_location, id",
            partitions,
        ).fetchall()
    finally:
        conn.close()
    out: list[Message] = []
    for r in rows:
        hw = high_water.get(r["to_location"])
        if hw is None or r["id"] > hw:
            out.append(_row_to_message(r))
    return out


def mark_read(message_id: str, uuid: str, now_iso: str, session_label: str) -> bool:
    """Atomically flip a 'sent' message to 'read', stamping the reader. Returns
    True iff this call was the writer (first-writer-wins under WAL); False if the
    message was already non-'sent'."""
    conn = connect()
    try:
        with _immediate(conn):
            cur = conn.execute(
                "UPDATE messages SET status='read', read_at=?, read_by_uuid=?, "
                "read_by_session=? WHERE id=? AND status='sent'",
                (now_iso, uuid, session_label, message_id),
            )
            return cur.rowcount == 1
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/messaging/test_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/repository.py tests/messaging/test_repository.py
git commit -m "feat(ccmsg): repository sweep_new + first-writer-wins mark_read (R2)"
```

---

### Task 5: repository — `claim` (first-claim-wins)

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/repository.py`
- Test: `tests/messaging/test_repository.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/messaging/test_repository.py
from cc_session_tools.lib.messaging.lock import AlreadyClaimedError


def test_claim_flips_and_stamps(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", to_kind="description", to_value="X",
                     to_location="_global"))
    m = repo.claim("20260620T000000Z-0001", "me-uuid", "beta", "2026-06-20T05:00:00Z")
    assert m.status == "claimed"
    assert m.read_by_uuid == "me-uuid"
    assert m.claimed_at == "2026-06-20T05:00:00Z"
    assert m.read_at == "2026-06-20T05:00:00Z"  # back-filled from now


def test_claim_missing_raises_not_found(root: Path) -> None:
    with pytest.raises(repo.MessageNotFoundError):
        repo.claim("ghost", "u", "s", "2026-06-20T05:00:00Z")


def test_second_claim_raises_already_claimed(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", to_kind="description", to_value="X",
                     to_location="_global"))
    repo.claim("20260620T000000Z-0001", "me", "s", "2026-06-20T05:00:00Z")
    with pytest.raises(AlreadyClaimedError):
        repo.claim("20260620T000000Z-0001", "other", "s2", "2026-06-20T06:00:00Z")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/messaging/test_repository.py -k claim -v`
Expected: FAIL — `repository.claim` missing.

- [ ] **Step 3: Write the implementation**

Append to `repository.py` (import `AlreadyClaimedError` at top: `from cc_session_tools.lib.messaging.lock import AlreadyClaimedError`):

```python
def claim(message_id: str, uuid: str, session: str, now_iso: str) -> Message:
    """First-claim-wins: inside one BEGIN IMMEDIATE, verify the message is
    claimable and flip it to 'claimed'. Raises MessageNotFoundError for an
    unknown id, AlreadyClaimedError if already read/claimed/archived.

    Correctness comes from BEGIN IMMEDIATE serialising concurrent claimers; the
    SELECT and UPDATE see one consistent snapshot."""
    conn = connect()
    try:
        with _immediate(conn):
            row = conn.execute(
                "SELECT status FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise MessageNotFoundError(message_id)
            if row["status"] in ("claimed", "read", "archived"):
                raise AlreadyClaimedError(message_id)
            conn.execute(
                "UPDATE messages SET status='claimed', claimed_at=?, "
                "read_at=COALESCE(read_at, ?), read_by_uuid=?, read_by_session=? "
                "WHERE id=?",
                (now_iso, now_iso, uuid, session, message_id),
            )
            updated = conn.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        return _row_to_message(updated)
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/messaging/test_repository.py -k claim -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/repository.py tests/messaging/test_repository.py
git commit -m "feat(ccmsg): repository claim — first-claim-wins under BEGIN IMMEDIATE"
```

---

### Task 6: repository — `archive_one` + `archive_aged` (retention core, R1 statement)

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/repository.py`
- Test: `tests/messaging/test_repository.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/messaging/test_repository.py

def test_archive_one_flips_status(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", status="read",
                     read_at="2026-06-20T00:00:00Z"))
    m = repo.archive_one("20260620T000000Z-0001")
    assert m.status == "archived"


def test_archive_one_missing_raises(root: Path) -> None:
    with pytest.raises(repo.MessageNotFoundError):
        repo.archive_one("ghost")


def test_archive_aged_only_settled_older_than_cutoff(root: Path) -> None:
    # read 15 days ago -> archived; read 13 days ago -> stays; unread -> stays.
    repo.insert(_msg("20260101T000000Z-0001", status="read",
                     read_at="2026-06-05T00:00:00Z"))            # old
    repo.insert(_msg("20260101T000000Z-0002", status="read",
                     read_at="2026-06-17T00:00:00Z"))            # recent
    repo.insert(_msg("20260101T000000Z-0003", status="sent"))   # unread
    cutoff = "2026-06-06T00:00:00Z"  # now(2026-06-20) - 14d
    archived = repo.archive_aged("projects/alpha", cutoff)
    assert archived == ["20260101T000000Z-0001"]
    assert repo.get_by_id("20260101T000000Z-0002").status == "read"
    assert repo.get_by_id("20260101T000000Z-0003").status == "sent"


def test_archive_aged_uses_claimed_at_when_read_at_null(root: Path) -> None:
    repo.insert(_msg("20260101T000000Z-0004", to_kind="description", to_value="w",
                     to_location="projects/alpha", status="claimed", read_at=None,
                     claimed_at="2026-06-05T00:00:00Z"))
    assert repo.archive_aged("projects/alpha", "2026-06-06T00:00:00Z") == \
        ["20260101T000000Z-0004"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/messaging/test_repository.py -k archive -v`
Expected: FAIL — `repository.archive_one` / `archive_aged` missing.

- [ ] **Step 3: Write the implementation**

Append to `repository.py`:

```python
def archive_one(message_id: str) -> Message:
    """Manually archive a message (status flip). Raises MessageNotFoundError for
    an unknown id. Idempotent on an already-archived row."""
    conn = connect()
    try:
        with _immediate(conn):
            row = conn.execute(
                "SELECT id FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is None:
                raise MessageNotFoundError(message_id)
            conn.execute(
                "UPDATE messages SET status='archived' WHERE id=?", (message_id,)
            )
            updated = conn.execute(
                "SELECT * FROM messages WHERE id=?", (message_id,)
            ).fetchone()
        return _row_to_message(updated)
    finally:
        conn.close()


def archive_aged(partition: str, cutoff_iso: str) -> list[str]:
    """Archive every read/claimed message in ``partition`` whose settle time
    (claimed_at, else read_at) is at or before ``cutoff_iso``, in one atomic
    statement. Returns the archived ids (sorted).

    R1: a second concurrent sweep runs the identical UPDATE, matches 0 rows
    (those messages are already 'archived'), and neither crashes nor double-
    archives. Because this is a status flip, a claim that landed first keeps its
    claimed_at / read_by_uuid. Timestamps are fixed-width ISO-8601 Z strings, so
    lexical <= is chronological <=."""
    conn = connect()
    try:
        with _immediate(conn):
            cur = conn.execute(
                "UPDATE messages SET status='archived' "
                "WHERE to_location=? AND status IN ('read','claimed') "
                "AND COALESCE(claimed_at, read_at) IS NOT NULL "
                "AND COALESCE(claimed_at, read_at) <= ? "
                "RETURNING id",
                (partition, cutoff_iso),
            )
            ids = sorted(r["id"] for r in cur.fetchall())
        return ids
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/messaging/test_repository.py -k archive -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/repository.py tests/messaging/test_repository.py
git commit -m "feat(ccmsg): repository archive_one + atomic archive_aged (R1 statement)"
```

---

### Task 7: R1 concurrency test — retention races itself and a claim

**Files:**
- Create: `tests/messaging/test_repository_race.py`

This is the flagship test of the phase, matching `test_lock.py::test_race_has_exactly_one_winner` in rigor. It proves the SQL layer closes the double-unlink crash and never loses claim metadata.

- [ ] **Step 1: Write the test**

```python
# tests/messaging/test_repository_race.py
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import repository as repo
from cc_session_tools.lib.messaging.lock import AlreadyClaimedError
from cc_session_tools.lib.messaging.message import Message


def _aged_read(mid: str) -> Message:
    return Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location="projects/alpha",
        subject="s", sent_at="2026-06-01T00:00:00Z", status="read",
        read_at="2026-06-05T00:00:00Z", read_by_uuid="r", read_by_session="r",
        claimed_at=None, receipt_shown=False, thread=None, attachments=[], body="b",
    )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    return tmp_path


def test_concurrent_archive_aged_no_crash_archived_once(root: Path) -> None:
    repo.insert(_aged_read("20260101T000000Z-0001"))
    cutoff = "2026-06-06T00:00:00Z"
    results: list[list[str]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        try:
            results.append(repo.archive_aged("projects/alpha", cutoff))
        except Exception as exc:  # noqa: BLE001 - captured, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []                                  # no double-unlink crash
    winners = [r for r in results if r]                  # RETURNING gave the id to exactly one
    assert winners == [["20260101T000000Z-0001"]]
    assert repo.get_by_id("20260101T000000Z-0001").status == "archived"


def test_claim_and_retention_race_preserves_claim_metadata(root: Path) -> None:
    # A message that is claimable now AND aged-read: whichever wins, no crash and
    # no lost metadata. Insert as aged-read so retention is eligible; a claimer
    # races it. Either the claim wins (message ends claimed, metadata intact) or
    # retention wins first (claim then sees a terminal status -> AlreadyClaimed).
    repo.insert(_aged_read("20260101T000000Z-0002"))
    outcomes: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def claimer() -> None:
        barrier.wait()
        try:
            m = repo.claim("20260101T000000Z-0002", "claimer", "beta",
                           "2026-06-20T00:00:00Z")
            outcomes.append(f"claimed:{m.read_by_uuid}")
        except AlreadyClaimedError:
            outcomes.append("already")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def retainer() -> None:
        barrier.wait()
        try:
            repo.archive_aged("projects/alpha", "2026-06-06T00:00:00Z")
            outcomes.append("archived-sweep")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1, t2 = threading.Thread(target=claimer), threading.Thread(target=retainer)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert errors == []
    final = repo.get_by_id("20260101T000000Z-0002")
    assert final is not None
    # Metadata is never partially lost: a claimed row keeps its claimer; an
    # archived-without-claim row keeps its original reader.
    if final.status == "claimed":
        assert final.read_by_uuid == "claimer"
    else:
        assert final.status == "archived"
        assert final.read_by_uuid in ("claimer", "r")
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/messaging/test_repository_race.py -v`
Expected: PASS (both race tests, no flakiness — WAL + busy_timeout=5000 serialise writers).

- [ ] **Step 3: Commit**

```bash
git add tests/messaging/test_repository_race.py
git commit -m "test(ccmsg): R1 retention-vs-claim race — no crash, no lost metadata"
```

---

### Task 8: repository — receipts, cursor table, display-tag refresh; drop dead helpers from `store.py`/`message.py`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/repository.py`
- Modify: `src/cc_session_tools/lib/messaging/store.py`
- Modify: `src/cc_session_tools/lib/messaging/message.py`
- Test: `tests/messaging/test_repository.py`, `tests/messaging/test_message.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/messaging/test_repository.py

def test_pending_receipts_only_unshown_read_or_claimed(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", from_uuid="me", status="read",
                     read_at="2026-06-20T00:00:00Z", read_by_session="rA"))
    repo.insert(_msg("20260620T000000Z-0002", from_uuid="me", status="sent"))
    repo.insert(_msg("20260620T000000Z-0003", from_uuid="other", status="read",
                     read_at="2026-06-20T00:00:00Z"))
    pending = repo.pending_receipts("me")
    assert [m.id for m in pending] == ["20260620T000000Z-0001"]


def test_mark_receipts_shown_is_idempotent(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", from_uuid="me", status="read",
                     read_at="2026-06-20T00:00:00Z"))
    repo.mark_receipts_shown(["20260620T000000Z-0001"])
    assert repo.pending_receipts("me") == []
    repo.mark_receipts_shown(["20260620T000000Z-0001"])  # no-op, no error


def test_cursor_load_save_round_trip(root: Path) -> None:
    assert repo.load_cursor("uuid-1") == {}
    repo.save_cursor("uuid-1", {"projects/alpha": "20260620T120000Z-0002"})
    assert repo.load_cursor("uuid-1") == {"projects/alpha": "20260620T120000Z-0002"}
    # upsert overwrites the same (uuid, partition)
    repo.save_cursor("uuid-1", {"projects/alpha": "20260620T130000Z-0003"})
    assert repo.load_cursor("uuid-1")["projects/alpha"] == "20260620T130000Z-0003"


def test_refresh_display_tags_updates_sender_and_reader(root: Path) -> None:
    repo.insert(_msg("20260620T000000Z-0001", from_uuid="u", from_session="old"))
    repo.insert(_msg("20260620T000000Z-0002", from_uuid="z", read_by_uuid="u",
                     read_by_session="old", status="read",
                     read_at="2026-06-20T00:00:00Z"))
    repo.insert(_msg("20260620T000000Z-0003", from_uuid="u", from_session="old",
                     status="archived"))  # archived: untouched
    n = repo.refresh_display_tags("u", "new-tag")
    assert n == 2
    assert repo.get_by_id("20260620T000000Z-0001").from_session == "new-tag"
    assert repo.get_by_id("20260620T000000Z-0002").read_by_session == "new-tag"
    assert repo.get_by_id("20260620T000000Z-0003").from_session == "old"
```

In `tests/messaging/test_message.py`: remove any test of `write_atomic` / `write_text_atomic` / `safe_parse` (those helpers are deleted). Keep the `serialise`/`parse` round-trip tests unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/messaging/test_repository.py -k "receipt or cursor or refresh" -v`
Expected: FAIL — helpers missing.

- [ ] **Step 3: Write the implementation**

Append to `repository.py`:

```python
def pending_receipts(from_uuid: str) -> list[Message]:
    """Messages this uuid sent that have been read/claimed but whose receipt has
    not yet been surfaced. An indexed lookup (idx_messages_receipts) replacing
    the old full-store filesystem scan on every hook fire."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE from_uuid=? AND status IN ('read','claimed') "
            "AND receipt_shown=0 ORDER BY id",
            (from_uuid,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]


def mark_receipts_shown(ids: list[str]) -> None:
    if not ids:
        return
    conn = connect()
    try:
        with _immediate(conn):
            conn.executemany(
                "UPDATE messages SET receipt_shown=1 WHERE id=?",
                [(mid,) for mid in ids],
            )
    finally:
        conn.close()


def load_cursor(session_uuid: str) -> dict[str, str]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT partition, high_water_message_id FROM cursors WHERE session_uuid=?",
            (session_uuid,),
        ).fetchall()
    finally:
        conn.close()
    return {r["partition"]: r["high_water_message_id"] for r in rows}


def save_cursor(session_uuid: str, high_water: dict[str, str]) -> None:
    if not high_water:
        return
    conn = connect()
    try:
        with _immediate(conn):
            conn.executemany(
                "INSERT INTO cursors (session_uuid, partition, high_water_message_id) "
                "VALUES (?,?,?) ON CONFLICT(session_uuid, partition) "
                "DO UPDATE SET high_water_message_id=excluded.high_water_message_id",
                [(session_uuid, p, hw) for p, hw in high_water.items()],
            )
    finally:
        conn.close()


def refresh_display_tags(uuid: str, new_tag: str) -> int:
    """Re-stamp from_session / read_by_session for non-archived messages
    referencing ``uuid``, in one targeted UPDATE. Returns the count changed."""
    conn = connect()
    try:
        with _immediate(conn):
            cur = conn.execute(
                "UPDATE messages SET "
                "from_session = CASE WHEN from_uuid=:u AND from_session<>:t "
                "THEN :t ELSE from_session END, "
                "read_by_session = CASE WHEN read_by_uuid=:u AND read_by_session<>:t "
                "THEN :t ELSE read_by_session END "
                "WHERE status<>'archived' AND "
                "((from_uuid=:u AND from_session<>:t) OR "
                " (read_by_uuid=:u AND read_by_session<>:t)) "
                "RETURNING id",
                {"u": uuid, "t": new_tag},
            )
            return len(cur.fetchall())
    finally:
        conn.close()
```

Now delete the dead flat-file helpers. In `store.py` remove: `CURSORS_DIRNAME`, `partition_dir`, `ensure_inbox_dir`, `archive_dir`, `cursors_dir`, `message_filename`. Keep `store_root`, `db_path`, `generate_id`, `slug_subject`, `other_paths_slug`, `partition_for_cwd`, `partition_for_project`, `GLOBAL_PARTITION` (`slug_subject` is still used by `other_paths_slug`). In `message.py` remove: `write_text_atomic`, `write_atomic`, `safe_parse`, and the now-unused `logging`/`Path` imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/messaging/test_repository.py tests/messaging/test_message.py -v`
Expected: PASS. (Other messaging tests may still fail here — they are rewritten in Tasks 9–14. That is expected mid-phase; run the targeted files only for this step.)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/repository.py \
        src/cc_session_tools/lib/messaging/store.py \
        src/cc_session_tools/lib/messaging/message.py \
        tests/messaging/test_repository.py tests/messaging/test_message.py
git commit -m "feat(ccmsg): repository receipts/cursor/tag-refresh; drop dead flat-file helpers"
```

---

### Task 9: rewrite `cursor.py` onto the cursor table

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/cursor.py`
- Test: `tests/messaging/test_cursor.py` (round-trip tests keep passing unchanged)

- [ ] **Step 1: Confirm the target tests**

`test_cursor.py::test_save_and_load_round_trip` and `test_load_missing_cursor_is_empty` already exercise the public API and must keep passing verbatim (they only set `CCST_MESSAGES_ROOT`). No test edit needed — this is a pure backend swap behind a stable interface.

- [ ] **Step 2: Run to verify current failure**

Run: `uv run pytest tests/messaging/test_cursor.py -v`
Expected: FAIL — `cursor.save`/`load` still import the removed `store.cursors_dir` / `message.write_text_atomic`.

- [ ] **Step 3: Rewrite the implementation**

```python
# src/cc_session_tools/lib/messaging/cursor.py
"""Per-session delivery cursor. Keyed on the stable session uuid so a rename
never resets it. Stores a per-partition high-water id; a message is new to a
session iff its (sortable) id exceeds the stored high-water for its partition.
Backed by the cursors table in ccmsg.db."""
from __future__ import annotations

from dataclasses import dataclass, field

from cc_session_tools.lib.messaging import repository
from cc_session_tools.lib.messaging.message import Message


@dataclass(frozen=True)
class Cursor:
    high_water: dict[str, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> Cursor:
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


def load(session_uuid: str) -> Cursor:
    return Cursor(high_water=repository.load_cursor(session_uuid))


def save(session_uuid: str, cursor: Cursor) -> None:
    repository.save_cursor(session_uuid, cursor.high_water)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/messaging/test_cursor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/cursor.py
git commit -m "refactor(ccmsg): back cursor.py with the cursors table"
```

---

### Task 10: rewrite `service.py` reads — `send`, `find_by_id`, `read_one`, `list_messages`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/service.py`
- Test: `tests/messaging/test_service.py`

- [ ] **Step 1: Update the tests**

In `test_service.py`, the send/read/list tests assert against the filesystem inbox. Rewrite those assertions to use the service/DB. Examples:

```python
def test_send_writes_message_to_partition_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(_sender())
    m = service.read_one(mid)
    assert m is not None
    assert m.to_location == "projects/alpha"
    assert m.status == "sent"
    assert m.subject == "Hello there"
    assert m.attachments == ["/abs/a.md"]
```

`test_list_messages_skips_malformed_file` is no longer meaningful (a DB row cannot be a half-written malformed file). Replace it with a listing-order/return-shape test:

```python
def test_list_messages_returns_compact_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    service.send(_sender())
    rows = service.list_messages()
    assert len(rows) == 1
    assert rows[0].to_kind == "project" and rows[0].to_value == "alpha"
```

Keep `test_read_one_returns_message`, `test_read_one_missing_id_returns_none`, `test_list_messages_filters_by_*` — they use only the public API and pass unchanged.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/messaging/test_service.py -k "send or read_one or list_messages" -v`
Expected: FAIL — service still imports removed store helpers.

- [ ] **Step 3: Rewrite the implementation**

Replace the store-touching parts of `service.py`. New imports (drop `ensure_inbox_dir`, `archive_dir`, `message_filename`, `store_root`, `write_atomic`, `safe_parse`, `parse`, `Path`):

```python
from cc_session_tools.lib.messaging import repository
from cc_session_tools.lib.messaging.repository import MessageNotFoundError
```

Rewrite `send`, `find_by_id`, `read_one`, `list_messages`; delete `_iter_message_files` and the module-level `MessageNotFoundError` class (now imported from `repository`, preserving `service.MessageNotFoundError`):

```python
def send(request: SendRequest) -> str:
    message_id = generate_id()
    message = Message(
        id=message_id, schema=1, from_project=request.from_project,
        from_session=request.from_session, from_uuid=request.from_uuid,
        to_kind=request.to_kind, to_value=request.to_value,
        to_location=request.to_partition, subject=request.subject,
        sent_at=_now_iso(), status="sent", read_at=None, read_by_uuid=None,
        read_by_session=None, claimed_at=None, receipt_shown=False,
        thread=request.thread, attachments=list(request.attachments),
        body=request.body,
    )
    repository.insert(message)
    return message_id


def find_by_id(message_id: str) -> Message | None:
    """Single indexed primary-key lookup (was a full rglob scan)."""
    return repository.get_by_id(message_id)


def read_one(message_id: str) -> Message | None:
    return repository.get_by_id(message_id)


def list_messages(
    *, status: str | None = None, partition: str | None = None,
    from_uuid: str | None = None,
) -> list[MessageRow]:
    return [
        MessageRow(
            id=m.id, status=m.status, to_kind=m.to_kind, to_value=m.to_value,
            from_session=m.from_session, subject=m.subject,
        )
        for m in repository.list_rows(status=status, partition=partition, from_uuid=from_uuid)
    ]
```

Note: `read_one` no longer parses a file, so it cannot raise `ValueError`/`OSError` from frontmatter
parsing — but it CAN now raise `sqlite3.Error` (e.g. `OperationalError` on a lock-contended DB
past `busy_timeout`). The CLI's `_cmd_read` try/except must widen from
`except (ValueError, OSError)` to `except (ValueError, OSError, sqlite3.Error)` — this is a
required production edit (added after adversarial review), not the harmless no-op it would have
been if `sqlite3.Error` weren't a new possible exception type post-migration. See Task 15's
updated Step 3.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/messaging/test_service.py -k "send or read_one or list_messages" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/service.py tests/messaging/test_service.py
git commit -m "refactor(ccmsg): service send/read/list on repository (indexed, no rglob)"
```

---

### Task 11: rewrite `service.claim` + `service.archive` (keep `claim_lock`, R4)

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/service.py`
- Test: `tests/messaging/test_service.py`

- [ ] **Step 1: Update the tests**

`test_claim_flips_status_and_blocks_second_claimer`, `test_archive_moves_message`, and `test_archive_blocked_while_claim_lock_held` mostly use the public API. Update `test_archive_moves_message`'s filesystem assertion (`list((tmp_path/…/"archive").rglob("*.md"))`) to a status check:

```python
def test_archive_moves_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone
    mid = _send_to_session_in_tmp(monkeypatch, tmp_path, "me-uuid")
    service.archive(mid, datetime(2026, 6, 20, tzinfo=timezone.utc))
    result = service.read_one(mid)
    assert result is not None
    assert result.status == "archived"
```

`test_archive_blocked_while_claim_lock_held` passes unchanged (it holds `claim_lock(mid)` and expects `AlreadyClaimedError` — the retained R4 lock still guards manual archive).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/messaging/test_service.py -k "claim or archive" -v`
Expected: FAIL.

- [ ] **Step 3: Rewrite the implementation**

```python
def claim(message_id: str, claimer: Claimer) -> Message:
    """First-claim-wins. The file-based claim_lock (R4, kept outside the DB) is
    the coarse envelope; repository.claim provides the atomic state transition."""
    now = _now_iso()
    with claim_lock(message_id):
        return repository.claim(message_id, claimer.uuid, claimer.session, now)


def archive(message_id: str, now: datetime) -> Message:
    """Manual archive. Acquires claim_lock so it cannot race a concurrent claim
    (R4), then flips status atomically."""
    with claim_lock(message_id):
        return repository.archive_one(message_id)
```

Delete the now-unused `now` computation branches, `parse`, and `archive_dir` references that remain. `archive`'s `now` param is retained for interface stability (the CLI passes `datetime.now(timezone.utc)`); it is unused internally now — annotate it so:

```python
def archive(message_id: str, now: datetime) -> Message:  # noqa: ARG001 - kept for CLI signature stability
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/messaging/test_service.py -k "claim or archive" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/service.py tests/messaging/test_service.py
git commit -m "refactor(ccmsg): service claim/archive on repository, claim_lock kept (R4)"
```

---

### Task 12: rewrite `service.deliver` + `_collect_receipts`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/service.py`
- Test: `tests/messaging/test_service.py`

- [ ] **Step 1: Update the tests**

The deliver tests (`test_deliver_auto_reads_session_message_once`, `…_does_not_auto_read_other_sessions_message`, `…_surfaces_receipt_once_to_sender`, `…_surfaces_description_as_proposal_without_reading`) use only the public API and pass unchanged. `test_deliver_skips_malformed_file_in_swept_partition` is no longer meaningful — replace it with a two-reader determinism test (R2) at service level:

```python
def test_deliver_project_message_auto_read_by_first_session_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="project", to_value="alpha", to_partition="projects/alpha",
        subject="team ping", body="b", attachments=[], thread=None,
    ))
    a = _ctx(uuid="sess-A", project="alpha", partition="projects/alpha")
    b = _ctx(uuid="sess-B", project="alpha", partition="projects/alpha")
    d_a = service.deliver(a, mode="full")
    d_b = service.deliver(b, mode="full")
    assert mid in d_a and mid not in d_b          # first reader wins the digest line
    read = service.read_one(mid)
    assert read is not None and read.read_by_uuid == "sess-A"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/messaging/test_service.py -k deliver -v`
Expected: FAIL — deliver still uses removed sweep/`safe_parse`/`write_atomic` code.

- [ ] **Step 3: Rewrite the implementation**

```python
def deliver(ctx: SessionContext, *, mode: DeliverMode) -> str:
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = cursor_mod.load(ctx.uuid)
    inbound: list[str] = []
    proposals: list[str] = []

    partitions = _swept_partitions(ctx)
    for message in repository.sweep_new(partitions, cur.high_water):
        kind = targets(message, ctx)
        if kind is MatchKind.RECIPIENT:
            # First-writer-wins (R2): only the session that actually flips
            # sent->read surfaces the line and back-fills read_by_session.
            if repository.mark_read(message.id, ctx.uuid, now_iso, ctx.project):
                inbound.append(_digest_line(message, now))
            cur = cursor_mod.advance(cur, message)
        elif kind is MatchKind.CANDIDATE:
            proposals.append(_digest_line(message, now))
            cur = cursor_mod.advance(cur, message)

    # Retention runs once per swept partition (opportunistic, atomic per R1).
    for partition in partitions:
        retention.archive_old(partition, now)

    cursor_mod.save(ctx.uuid, cur)
    receipts = _collect_receipts(ctx, now)
    return _format_digest(inbound, proposals, receipts)


def _collect_receipts(ctx: SessionContext, now: datetime) -> list[str]:
    pending = repository.pending_receipts(ctx.uuid)
    lines: list[str] = []
    shown: list[str] = []
    for message in pending:
        who = message.read_by_session or "a session"
        lines.append(
            f'✓ read: "{message.subject}" by {who} '
            f"({_relative_age(message.sent_at, now)}) [{message.id}]"
        )
        shown.append(message.id)
    repository.mark_receipts_shown(shown)
    return lines
```

Keep `_swept_partitions`, `_digest_line`, `_relative_age`, `_format_digest` unchanged. Note the RECIPIENT branch preserves the original auto-read attribution comment about `read_by_session` carrying the project label (now `ctx.project` passed to `mark_read`).

**Known, accepted inefficiency (found by adversarial review) — one connection per newly-delivered message.** The `for message in repository.sweep_new(...)` loop calls `repository.mark_read()` once per RECIPIENT-matched message, and each `mark_read` opens a fresh `repository.connect()` — which re-runs the full schema DDL via `lib.db.connect`'s `executescript(_DDL)` on every open (the DDL is `CREATE TABLE/INDEX IF NOT EXISTS`, so it is idempotent but not free). This is an N+1-connection pattern, but N is the count of *not-yet-read* messages surfaced in a single sweep — normally 0-2, occasionally a handful after a long absence — not the whole store, so it is bounded and not worth complicating `deliver`/`mark_read` with a shared-connection or batch-UPDATE refactor. Documented here only so a future reader recognises the repeated-`connect()` pattern as a deliberate, bounded tradeoff (single-writer atomicity per message via `BEGIN IMMEDIATE`) rather than an oversight — no code change required.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/messaging/test_service.py -v`
Expected: PASS (whole service file).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/service.py tests/messaging/test_service.py
git commit -m "refactor(ccmsg): deliver/receipts on indexed queries; R2 digest determinism"
```

---

### Task 13: rewrite `retention.archive_old`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/retention.py`
- Test: `tests/messaging/test_retention.py`

- [ ] **Step 1: Update the tests**

`test_retention.py`'s `_write` helper builds a file via `write_atomic` + `message_filename` (both removed). Rewrite `_write` to insert via the repository, and update the archive assertions to check status instead of file moves:

```python
from cc_session_tools.lib.messaging import repository, retention
from cc_session_tools.lib.messaging.message import Message


def _write(partition: str, mid: str, status: str, stamp: str | None) -> None:
    repository.insert(Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location=partition, subject="s",
        sent_at="2026-06-01T00:00:00Z", status=status,  # type: ignore[arg-type]
        read_at=stamp, read_by_uuid="r" if stamp else None,
        read_by_session="r" if stamp else None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    ))


def test_read_15_days_old_is_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    _write("projects/alpha", "20260101T000000Z-0001", "read", _iso(now - timedelta(days=15)))
    assert retention.archive_old("projects/alpha", now) == ["20260101T000000Z-0001"]
    m = repository.get_by_id("20260101T000000Z-0001")
    assert m is not None and m.status == "archived"
```

Update the `unread`, `13-days`, and `claimed_at` tests the same way (insert via `_write`/repository, assert via `repository.get_by_id(...).status` and `retention.archive_old(...) == [...]`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/messaging/test_retention.py -v`
Expected: FAIL.

- [ ] **Step 3: Rewrite the implementation**

```python
# src/cc_session_tools/lib/messaging/retention.py
"""Opportunistic retention: archive read/claimed messages older than 14 days.

Archiving is a status flip (never a delete), done in one atomic statement so a
concurrent sweep or claim can neither crash it nor lose claim metadata (R1).
Unread messages never expire. Called from deliver with bounded per-sweep cost."""
from __future__ import annotations

from datetime import datetime, timedelta

from cc_session_tools.lib.messaging import repository

_RETENTION_DAYS = 14


def archive_old(partition: str, now: datetime) -> list[str]:
    """Archive eligible messages in ``partition``. Returns the archived ids."""
    cutoff = (now - timedelta(days=_RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return repository.archive_aged(partition, cutoff)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/messaging/test_retention.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/retention.py tests/messaging/test_retention.py
git commit -m "refactor(ccmsg): retention.archive_old delegates to atomic archive_aged"
```

---

### Task 14: rewrite `move_safety.py`

**Files:**
- Modify: `src/cc_session_tools/lib/messaging/move_safety.py`
- Test: `tests/messaging/test_move_safety.py` (create if absent, else update)

- [ ] **Step 1: Write/update the test**

```python
# tests/messaging/test_move_safety.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import move_safety, repository
from cc_session_tools.lib.messaging.message import Message


def _msg(mid: str, **over) -> Message:
    base = dict(
        id=mid, schema=1, from_project="p", from_session="old", from_uuid="u",
        to_kind="project", to_value="alpha", to_location="projects/alpha",
        subject="s", sent_at="2026-06-20T00:00:00Z", status="sent", read_at=None,
        read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    )
    base.update(over)
    return Message(**base)


def test_refresh_display_tags_updates_pending_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    repository.insert(_msg("20260620T000000Z-0001", from_uuid="u", from_session="old"))
    repository.insert(_msg("20260620T000000Z-0002", from_uuid="u", from_session="old",
                           status="archived"))
    assert move_safety.refresh_display_tags(uuid="u", new_tag="new") == 1
    assert repository.get_by_id("20260620T000000Z-0001").from_session == "new"
    assert repository.get_by_id("20260620T000000Z-0002").from_session == "old"


def test_relocate_cursor_is_noop_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    move_safety.relocate_cursor(uuid="u", old_partition="a", new_partition="b")  # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/messaging/test_move_safety.py -v`
Expected: FAIL — `move_safety` imports removed `_iter_message_files` / `safe_parse` / `write_atomic`.

- [ ] **Step 3: Rewrite the implementation**

```python
# src/cc_session_tools/lib/messaging/move_safety.py
"""Rename/move safety for the message store, called by the move-session skill.

uuid routing means no message is ever orphaned by a rename; these helpers keep
the cosmetic display tag fresh and give the move flow an explicit cursor hook."""
from __future__ import annotations

from cc_session_tools.lib.messaging import cursor as cursor_mod
from cc_session_tools.lib.messaging import repository


def refresh_display_tags(*, uuid: str, new_tag: str) -> int:
    """Update from_session / read_by_session for pending (non-archived) messages
    referencing ``uuid``. Returns the count updated (one targeted UPDATE)."""
    return repository.refresh_display_tags(uuid, new_tag)


def relocate_cursor(*, uuid: str, old_partition: str, new_partition: str) -> None:
    """The cursor is uuid-keyed, so it survives a project move unchanged. This
    explicit call site exists for the move-session flow and future rekeying."""
    _ = (old_partition, new_partition)  # currently a no-op by design
    cursor_mod.save(uuid, cursor_mod.load(uuid))
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/messaging/test_move_safety.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/messaging/move_safety.py tests/messaging/test_move_safety.py
git commit -m "refactor(ccmsg): move_safety on repository targeted UPDATE"
```

---

### Task 15: CLI + delivery-hook black-box regression (interface unchanged)

**Files:**
- Modify (filesystem assertion replacement, see Step 2): `tests/messaging/test_ccmsg_cli.py`, `tests/messaging/test_messaging_deliver_hook.py`
- Modify (required production edit, added after adversarial review — see Step 1a):
  `src/cccs_hooks/messaging_deliver.py`, `src/cc_session_tools/cli/ccmsg.py`.

- [ ] **Step 1: Run the black-box CLI suite as-is**

Run: `uv run pytest tests/messaging/test_ccmsg_cli.py -v`
Expected: MOST pass unchanged (they assert on stdout/stderr/exit codes — the preserved contract). Two assert `(tmp_path/"projects"/"alpha"/"inbox").is_dir()` (`test_send_happy_path`, `test_send_body_file_happy_path`); those directories no longer exist.

- [ ] **Step 1a: Widen the exception guards to catch `sqlite3.Error` (required — closes a
  regression an adversarial review found: a lock-contended writer past `busy_timeout` now raises
  `sqlite3.OperationalError`, a subclass of `sqlite3.Error`, which the pre-migration
  `except (OSError, ValueError)` guards do not catch)**

In `src/cccs_hooks/messaging_deliver.py`, find the `except (OSError, ValueError):` guard around
`service.deliver(...)` and widen it:

```python
import sqlite3
# ...
    except (OSError, ValueError, sqlite3.Error) as exc:
```

In `src/cc_session_tools/cli/ccmsg.py`, find `_cmd_read`'s `except (ValueError, OSError) as exc:`
guard and widen it identically:

```python
import sqlite3
# ...
    except (ValueError, OSError, sqlite3.Error) as exc:
```

Add a regression test proving the hook degrades gracefully (rather than crashing) when the
underlying repository call raises a SQLite error, matching the existing pattern in
`test_messaging_deliver_hook.py` that already tests fail-open behaviour when `service.deliver`
raises:

```python
def test_hook_fails_open_on_sqlite_operational_error(monkeypatch, capsys):
    import sqlite3
    from cccs_hooks import messaging_deliver

    def _raise_locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(messaging_deliver.service, "deliver", _raise_locked)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
        "session_id": "s1", "cwd": "/tmp/x", "hook_event_name": "UserPromptSubmit",
    })))
    rc = messaging_deliver.main()
    assert rc == 0  # never blocks a session, even on a SQLite lock-contention error
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["additionalContext"] == ""
```

Run: `uv run pytest tests/messaging/test_messaging_deliver_hook.py::test_hook_fails_open_on_sqlite_operational_error -v`
Expected: PASS.

- [ ] **Step 2: Replace the two filesystem assertions**

Change each `assert (…/"inbox").is_dir()` to a behavioural check via the CLI:

```python
def test_send_happy_path(tmp_path: Path) -> None:
    res = _run([...], tmp_path)
    assert res.returncode == 0, res.stderr
    mid = res.stdout.strip()
    read = _run(["read", mid], tmp_path)
    assert read.returncode == 0
    assert "Hi" in read.stdout
```

For `test_send_routes_project_to_project_partition` (asserts `list((store_dir/"projects"/"alpha"/"inbox").glob("*.md"))`) assert via `ccmsg list --partition projects/alpha` returning one row instead.

- [ ] **Step 3: Run CLI + hook suites to verify pass**

Run: `uv run pytest tests/messaging/test_ccmsg_cli.py tests/messaging/test_messaging_deliver_hook.py -v`
Expected: PASS. The hook's fail-open behaviour is unchanged (`deliver` no longer raises
`FileNotFoundError` at all — R1 is closed at the source, not merely caught), but the exception
guard itself DID need the Step 1a widening — a genuinely required production edit, not a no-op.

- [ ] **Step 4: Commit**

```bash
git add src/cccs_hooks/messaging_deliver.py src/cc_session_tools/cli/ccmsg.py \
    tests/messaging/test_ccmsg_cli.py tests/messaging/test_messaging_deliver_hook.py
git commit -m "fix(ccmsg): widen hook/CLI exception guards to catch sqlite3.Error

A lock-contended writer past busy_timeout raises sqlite3.OperationalError, which the
pre-migration except (OSError, ValueError) guards do not catch — this would have broken
the hook's never-blocks-a-session invariant under real concurrency. Found by adversarial
plan review."
```

---

### Task 16: one-shot migration `scripts/migrate_ccmsg_to_db.py`

**Files:**
- Create: `scripts/migrate_ccmsg_to_db.py`
- Test: `tests/test_migrate_ccmsg_to_db.py`

Follows overview §4 exactly: write the DB **without touching old files**, verify row counts + spot-check, then `tar czf` the old tree to a backup outside it, then delete the old flat files. Never delete-as-you-go. `.locks/*.lock` present at migration time indicates an orphaned R4 lock: report and leave, never migrate as data.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrate_ccmsg_to_db.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import message, repository, store
from scripts.migrate_ccmsg_to_db import migrate


def _old_message_file(old_root: Path, partition: str, mid: str, subject: str) -> None:
    inbox = old_root / partition / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    m = message.Message(
        id=mid, schema=1, from_project="o", from_session="s", from_uuid="u",
        to_kind="project", to_value="alpha", to_location=partition,
        subject=subject, sent_at="2026-06-20T00:00:00Z", status="sent",
        read_at=None, read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=["/abs/a.md"], body="Body.",
    )
    (inbox / f"{mid}__slug.md").write_text(message.serialise(m), encoding="utf-8")


def test_migrate_moves_messages_and_cursors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(new_root))
    _old_message_file(old_root, "projects/alpha", "20260620T000000Z-0001", "One")
    _old_message_file(old_root, "_global", "20260620T000000Z-0002", "Two")
    cursors = old_root / ".cursors"
    cursors.mkdir(parents=True)
    (cursors / "uuid-1.json").write_text(
        json.dumps({"high_water": {"projects/alpha": "20260620T000000Z-0001"}}),
        encoding="utf-8",
    )
    backups = tmp_path / "backups"

    rc = migrate(old_root=old_root, backup_dir=backups, dry_run=False)
    assert rc == 0

    assert repository.get_by_id("20260620T000000Z-0001").subject == "One"
    assert repository.get_by_id("20260620T000000Z-0002").subject == "Two"
    assert repository.load_cursor("uuid-1") == {"projects/alpha": "20260620T000000Z-0001"}
    assert list(backups.glob("ccmsg-*.tar.gz"))         # backup taken
    assert not (old_root / "projects").exists()          # old tree removed after verify


def test_migrate_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_root = tmp_path / "old"
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path / "new"))
    _old_message_file(old_root, "projects/alpha", "20260620T000000Z-0001", "One")
    assert migrate(old_root=old_root, backup_dir=tmp_path / "b", dry_run=True) == 0
    assert not store.db_path().exists()
    assert (old_root / "projects").exists()              # untouched
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_migrate_ccmsg_to_db.py -v`
Expected: FAIL — `scripts.migrate_ccmsg_to_db` missing.

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""One-shot migration of the flat-file message store into ccmsg.db.

Reads the OLD partition tree (default ~/.claude/cc-messages), parses each
message via message.parse(), inserts one row per message into ccmsg.db (under
CCST_MESSAGES_ROOT / data_home()), and migrates .cursors/*.json into the cursors
table. Non-destructive by construction: write -> verify (row count matches file
count, spot-check content) -> tar-backup the old tree -> only then delete it.
Re-runnable: INSERT OR IGNORE keeps it idempotent on message id.

Live .locks/*.lock files are transient (released on process exit); one present
here indicates an orphaned crash-gap lock (R4). It is reported and left for
manual cleanup, never migrated as data.

Usage:
    python3 scripts/migrate_ccmsg_to_db.py [--old-root PATH] [--backup-dir PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import paths
from cc_session_tools.lib.messaging import message, repository, store

_DEFAULT_OLD_ROOT = Path.home() / ".claude" / "cc-messages"


def _default_backup_dir() -> Path:
    return paths.data_home() / "migration-backups"


def _iter_old_message_files(old_root: Path) -> list[Path]:
    return sorted(
        p for p in old_root.rglob("*.md")
        if p.is_file() and ".locks" not in p.parts
    )


def _insert_ignore(msg: message.Message) -> None:
    conn = repository.connect()
    try:
        with repository._immediate(conn):
            placeholders = ", ".join("?" for _ in repository._COLUMNS)
            conn.execute(
                f"INSERT OR IGNORE INTO messages "
                f"({', '.join(repository._COLUMNS)}) VALUES ({placeholders})",
                repository._insert_params(msg),
            )
    finally:
        conn.close()


def migrate(*, old_root: Path, backup_dir: Path, dry_run: bool) -> int:
    if not old_root.is_dir():
        print(f"Old store not found: {old_root} - nothing to migrate.", file=sys.stderr)
        return 1

    files = _iter_old_message_files(old_root)
    parsed: list[message.Message] = []
    skipped = 0
    for path in files:
        try:
            parsed.append(message.parse(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            skipped += 1
            print(f"  skip {path.name}: {exc}", file=sys.stderr)

    locks = list((old_root / ".locks").glob("*.lock")) if (old_root / ".locks").is_dir() else []
    for lock in locks:
        print(f"  WARNING orphaned claim lock (R4), left in place: {lock}", file=sys.stderr)

    cursor_files = sorted((old_root / ".cursors").glob("*.json")) if (old_root / ".cursors").is_dir() else []

    print(f"Found {len(parsed)} message(s) ({skipped} skipped), {len(cursor_files)} cursor file(s).")
    if dry_run:
        print(f"[dry-run] would write {len(parsed)} row(s) to {store.db_path()}")
        return 0

    # 1. Write DB (no old files touched).
    for msg in parsed:
        _insert_ignore(msg)
    for cf in cursor_files:
        data = json.loads(cf.read_text(encoding="utf-8"))
        hw = data.get("high_water") if isinstance(data, dict) else None
        if isinstance(hw, dict):
            repository.save_cursor(cf.stem, {str(k): str(v) for k, v in hw.items()})

    # 2. Verify before any deletion.
    db_count = len(repository.list_rows())
    if db_count != len(parsed):
        print(f"ABORT: DB row count {db_count} != parsed message count {len(parsed)}; "
              "old files left intact.", file=sys.stderr)
        return 2
    for sample in parsed[:5]:
        got = repository.get_by_id(sample.id)
        if got is None or got.subject != sample.subject or got.body != sample.body:
            print(f"ABORT: spot-check mismatch on {sample.id}; old files left intact.",
                  file=sys.stderr)
            return 2

    # 3. Tar-backup the old tree (outside it) only after verification passes.
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"ccmsg-{stamp}.tar.gz"
    with tarfile.open(backup, "w:gz") as tar:
        tar.add(old_root, arcname=old_root.name)
    print(f"Backed up old store to {backup}")

    # 4. Delete old flat files (message tree + cursors), leaving orphaned locks.
    for path in files:
        path.unlink(missing_ok=True)
    for cf in cursor_files:
        cf.unlink(missing_ok=True)
    # Remove now-empty partition/inbox/archive dirs, but keep .locks if it holds orphans.
    for d in sorted((p for p in old_root.rglob("*") if p.is_dir()), reverse=True):
        if ".locks" in d.parts:
            continue
        try:
            d.rmdir()
        except OSError:
            pass

    print(f"Migration complete: {len(parsed)} message(s), {len(cursor_files)} cursor(s). "
          f"DB: {store.db_path()}")
    if locks:
        print(f"{len(locks)} orphaned lock(s) left in {old_root / '.locks'} for manual review.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="migrate_ccmsg_to_db.py",
        description="Migrate the flat-file message store into ccmsg.db.",
    )
    p.add_argument("--old-root", default=None, metavar="PATH",
                   help=f"Old store root (default: {_DEFAULT_OLD_ROOT})")
    p.add_argument("--backup-dir", default=None, metavar="PATH",
                   help="Where the pre-deletion tar.gz backup is written "
                        "(default: <data_home>/migration-backups)")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would migrate without writing or deleting")
    args = p.parse_args(argv)
    return migrate(
        old_root=Path(args.old_root) if args.old_root else _DEFAULT_OLD_ROOT,
        backup_dir=Path(args.backup_dir) if args.backup_dir else _default_backup_dir(),
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: the migration reuses `repository._COLUMNS` / `_insert_params` / `_immediate` deliberately (single source of truth for the row shape) with an `INSERT OR IGNORE` variant for idempotency, rather than duplicating the column list. If the repo's lint forbids private cross-module access, promote these three to public names (`COLUMNS`, `insert_params`, `immediate`) in a follow-up refactor step; note it in `WORKLOG.md`.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_migrate_ccmsg_to_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_ccmsg_to_db.py tests/test_migrate_ccmsg_to_db.py
git commit -m "feat(ccmsg): one-shot flat-tree -> ccmsg.db migration (write/verify/backup/delete)"
```

---

## Verification

- [ ] **Full messaging suite green**

```bash
uv run pytest tests/messaging tests/test_repository.py tests/test_migrate_ccmsg_to_db.py -q
```

Expected: all pass, including the R1 race (`test_repository_race.py`) and R2 determinism tests.

- [ ] **Full repo suite green (no cross-subsystem regressions)**

```bash
uv run pytest -q
```

Expected: all pass. No non-messaging test imports the removed `store`/`message` helpers.

- [ ] **Lint + type-check the touched modules**

```bash
uv run ruff check src/cc_session_tools/lib/messaging scripts/migrate_ccmsg_to_db.py
uv run mypy src/cc_session_tools/lib/messaging scripts/migrate_ccmsg_to_db.py
```

(Confirm the exact configured commands in `pyproject.toml` / CI first and match them.)

- [ ] **Manual smoke test end-to-end**

```bash
export CCST_MESSAGES_ROOT="$(mktemp -d)"
uv run python -m cc_session_tools.cli.ccmsg send --to-project alpha --subject Hi --body B \
  --from-project o --from-session s --from-uuid u --from-partition projects/o --to-partition projects/alpha
uv run python -m cc_session_tools.cli.ccmsg list
# expect one [id] sent    project=alpha · Hi line, and a ccmsg.db file in $CCST_MESSAGES_ROOT
ls "$CCST_MESSAGES_ROOT"
```

Expected: `ccmsg.db` present; `list`/`read`/`claim`/`archive` behave exactly as before the migration.

## Handoff

Phase 2 is complete when the whole suite is green, `ccmsg.db` is the only on-disk artefact under `CCST_MESSAGES_ROOT`, the `ccmsg` CLI contract is byte-identical, and the migration script has been run + verified on the real machine (`python3 scripts/migrate_ccmsg_to_db.py --dry-run` first, then for real). Phases 3–6 remain independent of this one; Phase 7 wires `ccst doctor` to health-check `ccmsg.db` alongside the other stores.
