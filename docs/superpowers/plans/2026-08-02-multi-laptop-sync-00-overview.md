# Multi-laptop sync — overview and plan index

> **For agentic workers:** this is an index, not an executable plan. Each plan below is
> independently implementable and independently testable — read this overview first for the
> binding cross-plan decisions (module names, file formats, sequencing), then open the plan file
> you're implementing. REQUIRED SUB-SKILL for every plan: `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans`.

**Goal:** let Chris move his `ccst pdata` per-project data between two laptops (Windows/WSL2 +
Mac) via explicit push/pull commands, flag `cc-sessions/<tag>/` folders that were created on the
*other* laptop when listing/resuming them, and give the machine-local CCST-infra databases
(`sessions.db`, `ccmsg.db`, `ccsched.db`, `telemetry.db`, `command-cache.db`) a local disaster-
recovery backup.

**Source spec** (read for full rationale and the settled Q&A this overview doesn't repeat):
`<session working dir>/multi-laptop-sync-spec.md`

**Non-goals** (spec §3 — do not build any of this): cross-platform session *resume* (transcripts
stay machine-local, permanently), cross-machine `ccmsg`, any daemon/filesystem-watcher/automatic
sync. Every sync action in these plans is a manual, explicit CLI invocation.

## Plan index

| # | Plan | File | Depends on |
|---|---|---|---|
| 1 | Core sync: running-session gate + `ccst pdata sync push/pull` + snapshot management | `2026-08-02-multi-laptop-sync-01-core-sync.md` | — |
| 2 | Foreign-session detection: machine identity + origin-marker SessionStart hook + `ccd`/`ccr` changes | `2026-08-02-multi-laptop-sync-02-foreign-session-detection.md` | — |
| 3 | `ccst backup-state` local-only backup CLI + bundled ccsched job + staleness nudge | `2026-08-02-multi-laptop-sync-03-backup-state.md` | Plan 1 (its last task extends Plan 1's push/pull handlers) |

Plans 1 and 2 touch disjoint files and can be implemented in parallel by different sessions. Plan
3 can be implemented in parallel with Plan 1 up to (but not including) its final "wire the nudge
in" task, which requires Plan 1's `_cmd_pdata_sync_push`/`_cmd_pdata_sync_pull` to exist.

## Version

Current: `1.4.1` (`pyproject.toml:7`). This work adds new CLI verbs (`ccst pdata sync
push/pull`, `ccst backup-state`) and a new on-disk convention (`.pdata-sync/` inside each
project folder) but changes the format of no existing on-disk store — `project-db/<project>.db`
itself is untouched, and pre-existing `cc-sessions/<tag>/` folders without an origin marker keep
working via the documented fallback (Plan 2, §4.3). Per this repo's CLAUDE.md version policy this
is a **minor bump, to `1.5.0`** — not major, no FAIL-level doctor migration-guard is required.
Bump `pyproject.toml` and cut a `CHANGELOG.md` `## [1.5.0]` section as the last step of Plan 3
(the plan that lands last).

## Cross-plan decisions (binding on every plan)

### 1. New modules and what lives where

| Module | Owning plan | Responsibility |
|---|---|---|
| `src/cc_session_tools/lib/running_gate.py` | Plan 1 | "Is it safe to copy this DB file right now?" — process check + WAL/SHM check |
| `src/cc_session_tools/lib/pdata/sync.py` | Plan 1 | Snapshot create/list/restore/prune for one project's `.db` |
| `src/cc_session_tools/lib/machine_identity.py` | Plan 2 | Resolve/prompt/store this laptop's machine name |
| `src/cc_session_tools/lib/session_origin.py` | Plan 2 | Write/read a plain-text per-session origin marker (no classification logic — see revision note below) |
| `src/cccs_hooks/session_origin_marker.py` | Plan 2 | SessionStart hook — the one place the marker is ever written, once, idempotently |
| `src/cc_session_tools/lib/backup_state.py` | Plan 3 | Machine-local DB backup + `job_state` bookkeeping + staleness nudge |

No module above imports from another plan's new module except: Plan 3's final task imports
nothing new, it only edits `cli/ccst.py`'s existing pdata-sync handlers (built in Plan 1) to call
`backup_state.staleness_nudge()`.

### 2. Env-var conventions (one per new test seam, matching existing convention)

| Var | Meaning | Default |
|---|---|---|
| `CCST_MACHINE_NAME` | Override the stored/detected machine name | none (falls through to stored file, then hostname) |
| `CCST_PROJECT_DB_DIR` (existing) | Where `project-db/<project>.db` lives | `paths.data_home() / "project-db"` |
| `CCST_PROJECTS_ROOT` (existing) | Where `<project>/` source folders live — also where `.pdata-sync/` snapshots live | `Path.home() / "cc"` |
| `CCST_DATA_HOME` (existing) | Root for every machine-local `.db` | `Path.home() / ".local" / "share" / "claude"` |

No new env var is introduced for snapshot location — snapshots live under
`init_paths.default_projects_root()`, reusing the existing `CCST_PROJECTS_ROOT` test seam.

### 3. Copying a live `.db` file — always via `db.backup_to()`, never `shutil.copy`

`src/cc_session_tools/lib/db.py:92` (`backup_to(source_path, dest_path)`) already exists, is
already tested (`tests/test_db.py:139`), and its docstring already names this exact use case
("migration scripts' pre-cutover safety copies"). Every new copy path in Plans 1 and 3 (snapshot
push, snapshot restore, machine-state backup) calls this — never `shutil.copy`/`tarfile` — because
it uses SQLite's own backup API and is safe against a concurrent writer on the source. The
running-session gate (§4 below) is an *additional*, coarser safety net on top of this, not a
replacement for it.

### 4. Running-session gate is new code — nothing to extend

Confirmed during investigation: **no existing code anywhere in this repo checks "is a process
running" or "is this file open" (no `lsof`, no `psutil`, no `pgrep`)**. Plan 1 writes
`lib/running_gate.py` from scratch. No new dependency is added — `psutil` is not a project
dependency and both target platforms (Linux/WSL2, macOS — see `pyproject.toml`'s classifiers,
there is no Windows-native classifier) ship `pgrep`; `lsof` is treated as best-effort (skipped
with a debug note if absent, per spec §4.4's "and/or").

**Design note, flagged for Chris's sign-off, not silently resolved:** the spec's process check is
"no `claude` process running **on this machine**" (`pgrep -x claude`), not "no other process holds
this specific `.db` file open". Concretely: this means `ccst pdata sync push` cannot be run *from
inside* a live Claude Code session (its own `claude` process would always trip the gate) — it must
be run from a plain shell after quitting Claude Code entirely. This matches the workflow the spec
describes ("Chris does right before switching laptops"), but is worth confirming before Plan 1
lands, since it rules out "ask Claude Code to push for me" as a usage pattern. Plan 1's CLI error
message states this explicitly so it's never a silent/confusing failure.

### 5. Timestamp format (used by both snapshot naming and backup-state naming)

`time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())` — UTC, fixed-width, lexicographically sortable
(so "most recent 5" is a plain reverse `sorted()` on directory names, no parsing needed).

### 6. Test conventions (binding on every plan)

- CLI-level tests: real subprocess (`subprocess.run([sys.executable, "-m",
  "cc_session_tools.cli.ccst", ...])`), matching `tests/test_ccst_pdata_verify_cli.py` exactly —
  same `base_env` fixture shape (`CCST_PROJECT_DB_DIR`, `CCST_PROJECTS_ROOT` redirected to
  `tmp_path`).
- `ccr`/`ccd` in-process tests: import the module and call `.main([...])` with `monkeypatch`,
  matching `tests/test_cli_ccr.py`'s `fake_repos`/`_make_session`/`captured_launch` fixtures
  exactly — extend `_make_session` rather than duplicating it.
- Any test touching `running_gate` monkeypatches `subprocess.run` (or the specific `pgrep`/`lsof`
  wrapper functions) — never spawns/kills a real process to test the gate.
- No new global fake-`$HOME` fixture — reuse the per-file `monkeypatch.setenv(...)` pattern already
  used throughout.

### 7. Explicitly deferred (not in scope for any of these 3 plans)

The spec (§6) flags "should `ccst doctor` gain a check for pdata-sync staleness" as a nice-to-have,
not a settled requirement. **Deliberately left out of Plans 1-3** — none of the three goals in
spec §2 require it, and doctor already WARNs about `pdata verify` staleness
(`lib/doctor.py:553`, `check_pdata_verify`) which is a related but distinct signal. Revisit as a
follow-up plan if Chris wants it after living with plain `ccst pdata sync` for a while.

## Deliverables checklist (for the human tracking overall progress)

- [ ] Plan 1: `lib/running_gate.py` + `lib/pdata/sync.py` shipped and unit-tested; `ccst pdata sync
      push --project NAME` and `ccst pdata sync pull --project NAME [--force]` /
      `--all-projects [--force]` wired into `cli/ccst.py` and CLI-tested end-to-end
- [ ] Plan 2 (revised — see plan doc's revision note): `lib/machine_identity.py` +
      `lib/session_origin.py` + a new `session-origin` SessionStart hook shipped; the hook stamps
      every session with its originating machine once; `ccd`/`ccr` each do a one-query
      `sessions.db` row-existence check (never a listing-wide scan) to flag a named session with
      no local record; `ccr` refuses to resume one unless `--force`. `ccl`/`ccs` are untouched by
      this feature — a DB-driven listing can't reliably surface a session with no local row
      anyway, so tagging it there was abandoned.
- [ ] Plan 3: `ccst backup-state` command + `backup-state` bundled `ccsched` job (registered
      disabled, tracked via `job_state` only) + staleness nudge wired into Plan 1's push/pull
- [ ] `pyproject.toml` bumped to `1.5.0`, `CHANGELOG.md` `## [1.5.0]` section cut (Plan 3's last
      task)
