# Data-store SQLite uplift — overview and phase index

> **For agentic workers:** this is an index, not an executable plan. Each phase below has its
> own fully-detailed, bite-sized plan document. Read this overview first for cross-phase
> decisions (env vars, DB layout, sequencing), then open the phase file you're implementing.
> REQUIRED SUB-SKILL for every phase: `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans`.

**Goal:** migrate `claude-code-session-tools`'s flat-file/TOML/JSONL + hand-rolled-lock data
stores onto SQLite (WAL mode), one `.db` file per subsystem, all relocated under a new root
`~/.local/share/claude/` — replacing the current mix of `~/.claude/...`, `~/.cache/claude/...`,
and `~/.cache/cc-session-tools/...` paths.

**Source specs** (read for full rationale — this overview doesn't repeat it):
- `/mnt/c/Users/cfoge/OneDrive/claude/claude/cc-sessions/20260712-claude-finalise-common-extra-claude-data-store-requirements/out/data-stores-design-spec.md`
- `/mnt/c/Users/cfoge/OneDrive/claude/claude/cc-sessions/20260712-claude-finalise-common-extra-claude-data-store-requirements/out/ccst-migration-and-cli-update-spec.md`
- `/mnt/c/Users/cfoge/OneDrive/claude/claude/cc-sessions/20260712-claude-finalise-common-extra-claude-data-store-requirements/out/ccmsg-concurrency-requirements.md`
- `/mnt/c/Users/cfoge/OneDrive/claude/claude/cc-sessions/20260712-claude-finalise-common-extra-claude-data-store-requirements/out/ccsched-concurrency-requirements.md`
- `/mnt/c/Users/cfoge/OneDrive/claude/claude/cc-sessions/20260712-claude-finalise-common-extra-claude-data-store-requirements/out/ccst-claude-flags-cache-requirements.md`

**Hard constraint across every phase:** external CLI interfaces (`ccmsg`/`ccsched` arguments and
output shapes) must not change. Only the storage backend changes. Other sessions and hooks
depend on today's contract.

**Out of scope for this repo** (confirmed during investigation, do not touch):
- `statusline-usage.db` — lives in `claude-statusline-powerline-fork`, different repo/runtime.
- `~/.mcp-servers-last-security-review` reminder script — lives in `claude-code-config-sync`.
  This repo has zero code referencing the file; nothing to migrate here (§4 of the design spec).
- `~/.claude/context-overrides/<uuid>` — writer/reader both live outside this repo
  (`~/.claude/skills/context-override/`, `claude-code-config-sync`). Not touched by this plan.
- `~/.claude/usage-data/` — confirmed dead: no current code in this repo or
  `claude-usage-analytics` writes to it (a stale comment in
  `skills/reduce-persistent-context/scripts/usage.py:4` documents this explicitly). Nothing to
  migrate; may be deleted by hand outside this plan, not part of it.

## Branch state (checked 2026-07-13)

`f/claude-data-store-uplift` was already merged into `main` via PR #71 (unrelated markers-dir /
catch-up-replay-bounding / `ccst gc report` work) and local `main`/`origin/main` is now 1 commit
ahead of this branch tip. **First task of Phase 1: merge `main` into this branch** before writing
any new code, so the branch isn't diverged from its own already-merged history.

## Version

Current: `0.18.0`. This migration changes CLI surface (new `ccst telemetry query`, new
`ccst backup`-adjacent helpers) and storage/config contract → **minor bump to `0.19.0`** per this
repo's CLAUDE.md version policy. Bump `pyproject.toml` and cut a `CHANGELOG.md` section as the
last step of Phase 7.

## Phase sequencing (must run in this order; each phase is a separate PR/commit series)

| # | Phase | File | Depends on |
|---|---|---|---|
| 1 | Shared infra: `lib/db.py`, `lib/paths.py`, backup-checkpoint helper | `2026-07-13-data-store-uplift-01-shared-infra.md` | — |
| 2 | `ccmsg` → `ccmsg.db` | `2026-07-13-data-store-uplift-02-ccmsg.md` | Phase 1 |
| 3 | `ccsched` → `ccsched.db` | `2026-07-13-data-store-uplift-03-ccsched.md` | Phase 1 |
| 4 | `sessions.db` (new) + `ccl`/`ccr` rewrite + doctor-mutes + tags-migrate retirement | `2026-07-13-data-store-uplift-04-sessions-db.md` | Phase 1 |
| 5 | `telemetry.db` (new) + `ccst telemetry query` + catchup-hook read path | `2026-07-13-data-store-uplift-05-telemetry.md` | Phase 1, Phase 3 (shares `CC_SCHEDULER_DIR`-adjacent ledger code) |
| 6 | `command-cache.db` path move + `claude-flags` relocate + atomic-write fix | `2026-07-13-data-store-uplift-06-cache-flags.md` | Phase 1 |
| 7 | Cleanup: install/doctor hookup, `gc report` updates, tar-backup safety net, docs, version bump | `2026-07-13-data-store-uplift-07-cleanup-install.md` | Phases 1-6 |

Phases 2-6 are independent of each other and can be implemented in parallel by different sessions
once Phase 1 has landed. Phase 7 is the integration/cleanup pass and must go last.

## Cross-phase decisions (binding on every phase)

### 1. Root and path resolution

`lib/paths.py` (built in Phase 1) exposes:

```python
def data_home() -> Path
```

Resolves `CCST_DATA_HOME` env var if set, else `Path.home() / ".local" / "share" / "claude"`.
Every subsystem's default directory is `data_home()` — **all `.db` files and the scheduler's
lock files sit flat in this single directory**, matching the design spec's one-root/
one-file-per-subsystem layout (§7.2). No per-subsystem subdirectories.

### 2. Per-subsystem env-var conventions (binding — do not invent new patterns per phase)

Every subsystem keeps exactly **one** environment variable, consistent with the one-env-var-per-
test-seam convention already used throughout this codebase (`CCCS_CACHE_DB`, `CC_SCHEDULER_DIR`,
`CCST_MESSAGES_ROOT`). Where an existing var name already exists, it is **kept** (semantics
change: was a directory of flat files, becomes a directory containing one `.db` file) so the
migration doesn't force a second rename on top of the path move.

| Subsystem | Env var | Value | Default | DB filename |
|---|---|---|---|---|
| `ccmsg` | `CCST_MESSAGES_ROOT` (kept) | directory | `data_home()` | `ccmsg.db` |
| `ccsched` | `CC_SCHEDULER_DIR` (kept) | directory (also holds `.run.<job-id>.lock`) | `data_home()` | `ccsched.db` |
| `sessions.db` (new) | `CCST_SESSIONS_DIR` (new) | directory | `data_home()` | `sessions.db` |
| `telemetry.db` (new) | `CCCS_HOOKS_DIR` (kept — already used for `fires.jsonl`'s dir) | directory | `data_home()` | `telemetry.db` |
| `command-cache.db` | `CCCS_CACHE_DB` (kept — already a direct file path, not a dir) | file | `data_home() / "command-cache.db"` | n/a (var is the file) |
| `claude-flags` | `CCST_CLAUDE_FLAGS_DIR` (new) | directory | `data_home()` | `claude-flags.json` (flat file, not a DB — see Phase 6) |

Every phase's tests redirect via the subsystem's one env var, matching the existing convention
(`monkeypatch.setenv(...)`) — no new fake-`$HOME` fixture needed anywhere.

### 3. Connection-setup helper contract (built in Phase 1, consumed by Phases 2-6)

```python
# src/cc_session_tools/lib/db.py
def connect(path: Path, *, ddl: str | None = None, readonly: bool = False) -> sqlite3.Connection
def checkpoint(conn: sqlite3.Connection) -> None
def backup_to(source_path: Path, dest_path: Path) -> None
```

`connect()` sets `PRAGMA journal_mode=WAL`, an explicit `PRAGMA busy_timeout=5000`, and
`PRAGMA foreign_keys=ON`; runs `ddl` (a `CREATE TABLE/INDEX/VIEW IF NOT EXISTS` multi-statement
string) if given; sets `row_factory = sqlite3.Row`. Every phase's store module calls this once
per connection rather than repeating pragma setup — this is the fix for the exact drift
(`statusline-usage.db` shipping without WAL while `command-cache.db` got it right) the design
spec calls out in §7.3.

### 4. Migration-script safety (binding on Phases 2, 3, 4, 5 — every phase with pre-existing data)

Every migration script (one per phase, run manually once per machine — not part of `ccst
install`) must:
1. Write the new `.db` file(s) **without touching the old flat files**.
2. Verify: row counts match source file/entry counts, spot-check a sample of parsed content.
3. Only after verification passes, take a `tar czf` backup of the pre-migration flat-file tree
   to a location outside that tree (e.g. `~/.local/share/claude/migration-backups/<name>-<date>.tar.gz`).
4. Only then remove the old flat files.
Never delete-as-you-go. This mirrors design-spec §8.5 exactly — treat it as a hard requirement in
every phase's migration-script task, not a suggestion.

### 5. Concurrency requirements (binding on Phase 2 and Phase 3 schema/transaction design)

Read `ccmsg-concurrency-requirements.md` (R1-R4) and `ccsched-concurrency-requirements.md`
(R1-R4) in full before writing schema/transaction code for those two phases — both are already
embedded in the respective phase plan documents, but the source files carry the full "why".

### 6. Test conventions (binding on every phase)

- No new global fake-`$HOME` fixture. Follow the existing per-file
  `monkeypatch.setenv(<SUBSYSTEM_ENV_VAR>, str(tmp_path))` pattern (see `tests/test_cache_sqlite.py`
  for the canonical example of testing a SQLite-backed store this way).
- CLI-level tests run the module as a real subprocess
  (`subprocess.run([sys.executable, "-m", "cc_session_tools.cli.<name>", ...])`), matching
  `tests/messaging/test_ccmsg_cli.py` / `tests/scheduler/test_ccsched_cli.py` — preserve this
  black-box style for any CLI test added or modified.
- Every new/changed concurrency-sensitive code path (retention-vs-claim, registry/state RMW,
  reconcile-vs-worker) needs a real multi-thread or multi-process race test proving exactly one
  winner / no lost updates — matching the existing precedent
  `tests/messaging/test_lock.py::test_race_has_exactly_one_winner` and
  `tests/scheduler/test_lock.py::test_race_has_exactly_one_winner`.

### 7. Known pre-existing bugs each phase must not reintroduce

- **ccmsg retention double-unlink race** (Phase 2): today's `retention.archive_old()` has no
  guard around `path.unlink()` — two concurrent sweeps archiving the same aged message can raise
  an uncaught `FileNotFoundError` that crashes `ccmsg deliver` outright (not just degrades a
  digest). The SQLite version must make the flip-status-and-remove a single atomic `DELETE`/
  `UPDATE` inside one transaction, closing this for free — see Phase 2 plan §R1.
- **ccsched state.json wholesale rewrite** (Phase 3): today's `state.json` is read-modify-written
  in full 4-5 times per single job run (registration guard, set-in-flight, mid-run reload+save,
  clear-in-flight), each an O(all jobs) rewrite. SQLite replaces this with targeted single-row
  `UPDATE`s — see Phase 3 plan.
- **telemetry rotation/cursor desync** (Phase 5): today's cursor is a row-count index into
  `hook=="catchup"` lines re-filtered from `fires.jsonl` on every read; a rotation event
  (copy-to-`.1` + truncate) can make a stale stored count silently swallow genuinely-new
  post-rotation rows (crash-guarded via `min(offset, len(rows))` but not correctness-guarded).
  The SQLite version must use a monotonic row id or indexed timestamp, not a re-derived count —
  see Phase 5 plan.
- **claude-flags non-atomic write** (Phase 6): `_CACHE_FILE.write_text(...)` today has no
  tmp-swap; switch to the existing `write_json_atomic` helper (`hooks_install.py:69`).

### 8. `ccl`/`ccr`/`ccs` performance requirement (added 2026-07-13, binding on Phase 4)

`sessions.db` must deliver a **measurable, tested** performance improvement for session
**title/tag** listing and matching — not just a storage-format change that happens to be faster
in theory. Scope: the session-inventory enumeration and name/tag lookup used by `ccl --global`,
`ccr`, and `ccs` (uuid, tag, project path, last-active/last-opened). Explicitly **out of scope**:
full-text search of session **content** (transcript/message bodies) — `ccs --order-by update`'s
recursive mtime walk over a session's working files is a different, unrelated mechanism and may
continue walking the filesystem after this migration; that is not a regression against this
requirement. Phase 4's plan enforces this with a regression test
(`TestSessionEnumerationScaling`) asserting flat query cost as session count grows, not a
one-off manual benchmark — see `data-stores-design-spec.md` §7.2 for the canonical statement of
this requirement.

### 9. Store initialization (binding on Phase 7, informs every phase's own tests)

Every phase's store module must create its own schema on first connection (via `connect(path,
ddl=...)` in Phase 1's helper) — this is the "each script creates it on first access" option from
design-spec §8.3, chosen over a separate `ccst install` DB-provisioning step because it's the
pattern `command-cache.db` already proves works and needs no new install-flow surface. Phase 7
adds a `ccst doctor` check that all six stores can be opened/queried, as the closest equivalent
to an install-time verification step.

## Deliverables checklist (for the human tracking overall progress)

- [ ] Phase 1: `lib/db.py`, `lib/paths.py` shipped, unit-tested, no consumers yet
- [ ] Phase 2: `ccmsg.db` live, `ccmsg` CLI interface unchanged, migration script run + verified
- [ ] Phase 3: `ccsched.db` live, `ccsched` CLI interface unchanged, migration script run + verified
- [ ] Phase 4: `sessions.db` live, `ccl --global`/`ccr`/`ccs` **session title/tag lookup**
      measurably faster (flat-cost indexed query, not O(n) walk — enforced by a scaling
      regression test, not just asserted; explicitly does NOT cover session-content search,
      which is a separate, unchanged mechanism), doctor-mutes migrated
- [ ] Phase 5: `telemetry.db` live, `ccst telemetry query` shipped, rotation-desync bug closed
- [ ] Phase 6: `command-cache.db` relocated, `claude-flags.json` relocated + atomic
- [ ] Phase 7: install/doctor hookup, `gc report` updated, README/CLAUDE.md updated, `CHANGELOG.md`
      cut, version bumped to `0.19.0`
