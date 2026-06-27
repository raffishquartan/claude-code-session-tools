# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`ccst install-everything`** — runs all install steps (skills, hooks, shell,
  claude-md) in sequence and finishes with a `ccst doctor` health check. Dry run
  by default; pass `--apply` to write changes. Pass `--no-pypi` to skip the
  PyPI version-drift check in the final health check. Mirrors the
  `install-everything.sh` bootstrap script but as a first-class CLI command so
  it can be re-run idempotently after upgrades without needing the shell script.
- `ccst doctor` now prints a tip (`Run: ccst install-everything --apply`) when
  it finds WARN or FAIL items, so the fix is always one command away.

## [0.16.0] - 2026-06-27
  
### Changed

- Default path for telemetry log (`fires.jsonl`) and rotation slots changed from
  `~/.claude/hooks/` to `~/.cache/claude/logs/`. Override with `CCCS_HOOKS_DIR`.
- Default path for command-cache DB changed from `~/.claude/hooks/command-cache.db`
  to `~/.cache/claude/logs/command-cache.db`. Override with `CCCS_CACHE_DB`.
- `command-cache.csv` retired; data migrated into `command-cache.db` (see migration
  script `scripts/migrate_csv_to_db.py`).
- Default directory for 8-digit-gate skill markers changed from
  `~/.claude/hooks/markers/` to `~/.cache/claude/markers/`. Override with
  `CCCS_MARKERS_DIR` (falls back to `$XDG_CACHE_HOME/claude/markers`). Marker
  writers (e.g. the `do-tesco-shop` skill) must `touch` the new path.

### Added

- **`marker-allow` PreToolUse hook** (`cccs_hooks.marker_allow`). Returns a
  PreToolUse `allow` decision for *exactly* a bare `touch <markers-dir>/<name>`
  command, so marker-gated skills (e.g. do-tesco-shop) can refresh their
  short-lived TTL marker under `~/.claude/hooks/markers/` without a permission
  prompt. The match is deliberately tight: any shell metacharacter, extra
  argument, flag, or out-of-directory path disqualifies the command, which then
  falls through to the normal permission flow. The hook never denies or blocks.
  Registered on the `Bash` matcher ahead of `bash-security-review`.
- **`cccs_hooks.markers`** module exposing `markers_dir()` as the single source
  of truth for the skill-marker directory, shared by `confirm_8digit` (which
  honours fresh markers as gate exemptions) and `marker_allow`.

### Fixed

- **`catchup` and `messaging-deliver` hooks read the wrong stdin field for the
  event name.** They read `hookEventName` (camelCase - the *output* field name),
  but Claude Code supplies the event on stdin as `hook_event_name` (snake_case).
  The lookup always missed and fell back to a hardcoded default, so on every
  `UserPromptSubmit` the `catchup` hook echoed `hookEventName: "SessionStart"`,
  triggering `Hook returned incorrect event name: expected 'UserPromptSubmit'
  but got 'SessionStart'`. `messaging-deliver` had the mirror bug (defaulting to
  `UserPromptSubmit`, breaking SessionStart and forcing always-incremental
  delivery). Both now read `hook_event_name`. Hook tests now feed the real
  snake_case field and assert the echoed event matches the invoking event.
- **`ccd` could permanently lock a name tag.** When a session failed to start
  after `ccd` had created its `cc-sessions/<date>-<tag>/` scaffold (e.g. `claude`
  aborted on a malformed `settings.json`), re-running `ccd <tag>` refused with
  "already started today" while `ccr <tag>` could not resume a transcript that
  was never written - leaving the tag unusable for the rest of the day. `ccd`
  now reuses the existing directory when it belongs to an *empty* session (no
  transcript, or a transcript with no user-typed messages), recovering the tag.
  A directory whose transcript shows real user input is still treated as a
  genuine duplicate and rejected.

### Removed

- **No-emdash Stop hook** (`no_emdash.py`). The hook injected a correction
  prompt whenever an assistant response contained an em-dash, but in practice
  it was noisy and unreliable. Removed the hook module, its test, the
  `no-emdash` dispatcher verb and description, and the `Stop` bundle entry.
  Uninstall it from an existing settings.json with
  `ccst hooks uninstall --hook no-emdash --apply`.

## [0.15.1] - 2026-06-24

### Fixed

- `uv.lock` version reference updated to match `pyproject.toml` bump to 0.15.0.
- CHANGELOG version-reference link table updated to include all releases since v0.11.0.

### Documentation

- Add superpowers design documents: last-screenshot hook spec and design, Claude.md bootstrap redesign plan.

## [0.15.0] - 2026-06-24

### Added

- **SQLite command cache** (`cache.py` rewrite). Replaces the CSV-backed
  command cache with a WAL-mode SQLite database (`command-cache.db`).
  Two-key lookup: `exact_hash` (SHA-256 of the exact command string) and
  `norm_hash` (SHA-256 of the normalised form). Structurally identical
  commands (e.g. `git checkout feature/a` and `git checkout feature/b`)
  now share a cache entry without an exact-hash match. Stale entries
  (> 90 days) are pruned automatically on every write; `cache_revalidate`
  is removed (superseded by auto-prune). Concurrent-write safety via
  SQLite WAL mode.
- **Command normalisation** (`normalise.py`). Token-aware normalisation
  that collapses variable arguments — branch names, paths, version strings,
  UUIDs, dates, URLs, globs — into typed placeholders (`<ARGS>`, `<PATH>`,
  `<DATE>`, `<UUID>`, etc.). Covers `git`, `find`, `npm`/`pip`/`cargo`,
  and read-only builtins (`ls`, `cat`, `wc`, …).
- **Hook invocation analytics** (`stats.py`, `hook_invocations` table).
  Every `bash-security-review` invocation is now recorded in the SQLite
  cache DB: exit tier (0 allowlist / 2 cache / 3 Claude), verdict,
  whether a heuristic fired, exact hash, and elapsed milliseconds. A
  `cache_efficiency` view aggregates hits vs total by verdict and tier.
- **`cccs-stats` CLI.** New entry point (`cccs-stats`) for efficiency and
  verdict reporting. Reads the `cache_efficiency` view and `hook_invocations`
  table; outputs a human-readable summary and a `file://` URI for direct
  DB inspection.
- **No-emdash Stop hook** (`no_emdash.py`). Detects em-dashes (—) in
  assistant responses and injects a correction prompt to replace them with
  space-surrounded hyphens ( - ). Registered as a `Stop` hook in the bundle.
- **move-session `.tag` file lookup** (step 4 in JSONL disambiguation).
  After a RENAME the move-session skill writes `<uuid>.tag`; this step
  resolves the disambiguation window between RENAME and the user running
  `/rename` inside Claude Code (e.g. a MOVE immediately following a RENAME).

### Fixed

- `bash_security_review.py`: stale-entry filtering moved inside
  `cache_lookup` (was a redundant second check in the caller).
- `test_cache_sqlite.py`: concurrent-write test uses corruption-specific
  assertions (fire_count per distinct key) rather than exact-count assertion,
  eliminating a race-condition flake on macOS.

## [0.14.0] - 2026-06-22

### Added

- **Scheduled-task catch-up.** A new `ccsched` CLI registers local recurring
  jobs in `~/.claude/cc-scheduler/jobs.toml`. Jobs run on a declared cadence
  (`every:`/`every:@from=`/`daily@`/`weekly:`/`monthly:<dom>@`/`monthly:<dow>#<n>@`,
  incl. drift-free anchored fortnightly and nth-weekday-of-month) and are
  reconciled on Claude Code session activity: runs missed while the laptop was
  off are back-filled, coalesced per the job's `coalesce` setting (`one`/`each`).
  Subcommands: `add`, `list`, `edit`, `enable`, `disable`, `remove`, `run`,
  `status`, `sweep`.
- **Detached execution.** The `catchup` hook only reconciles + launches owed
  jobs as detached background workers (`ccsched _run-job`) and surfaces
  previously-completed runs — job commands never run on the session critical
  path, so a slow or numerous backlog never blocks or slows session start. A
  per-job `O_EXCL` in-flight lock (with stale-holder reclamation) is the sole
  overlap guarantee; there is no global sweep lock.
- **`catchup` hooks on both `SessionStart` and `UserPromptSubmit`.**
  SessionStart reconciles+launches+surfaces; UserPromptSubmit surfaces (reaps)
  and reconciles on a throttle, so a job launched at session start surfaces at
  the next prompt in the same session. Surfacing is per-session (per-session
  cursor). Failures never block the session; every action (launch/run/backfill/
  fail/…) is recorded to the shared `fires.jsonl` telemetry ledger.
- **`manage-recurring-cc-jobs-using-ccsched` skill** translates natural-language
  cadence requests into validated `ccsched add` calls and disambiguates `ccsched`
  vs `/schedule` (cloud cron) vs `/loop` (in-session poll).

## [0.13.0] - 2026-06-21

### Added

- **Inter-session messaging.** A new `ccmsg` CLI sends durable, addressed,
  auditable messages between Claude Code sessions (to a session, a project, or a
  free-text description), stored as markdown-with-frontmatter under
  `~/.claude/cc-messages/`. Subcommands: `send`, `deliver`, `read`, `list`,
  `claim`, `archive`. `ccmsg send` resolves the sender's session uuid from
  `$CLAUDE_CODE_SESSION_ID`, the display tag from `$CLD_SESSION_TAG`, and the
  project/partition from the cwd, and routes to the recipient's partition
  automatically, so a send needs only a recipient, subject, and body.
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

### Changed

- `ccst hooks install` now prints a `Hook | Status | Event | Description` table listing every bundled hook, its install status (`install` for new, `already-installed` for existing), the Claude Code event (and matcher) it fires on, and a brief note about what it does. Mirrors the existing `ccst skills install` table format. The `--hook <name>` selector filters the table to a single row.

See [TODO.md](TODO.md) for known follow-up work, including the `notify-user` skill
integration (push notifications when 8-digit confirmation gates fire).

## [0.12.0] - 2026-06-17

### Added

- **`last-screenshot` hook.** A `UserPromptSubmit` hook resolves the newest
  screenshot for the `>lss` token and injects its path into the prompt context.
- **`ccs` session activity tracking.** Records last-opened and last-active times
  per session and extends `ccs --order-by` to sort on them.

### Changed

- Gmail self-sends are now exempt from the 8-digit confirmation gate.
- `move-session` tags sessions with their session name (skill + README updates).

### Fixed

- `ccr` now resumes the correct session by UUID after a rename, and
  `move_session` writes the `.tag` file so renamed sessions stay resolvable.
- The 8-digit confirmation gate short-circuits non-gated tools before any
  verification work.
- `claude-code-usage` guards `_aggregate` against a missing `tool_calls` column.
- `pricing.json` is packaged inside the `claude_code_usage` module so pricing
  data ships with the wheel.

## [0.11.0] - 2026-05-16

### Added

**`ccs` enhancements:**
- List mode: no positional query and no search flags → list all sessions newest-first with exit 0; exit 1 + warning when no sessions exist.
- `--emptiness {only,exclude,any}` flag to filter by whether a session's JSONL transcript contains any user-typed messages. Default: `any` (no behavioural change for existing invocations). Sessions with missing or unreadable transcripts are treated conservatively as non-empty.
- Session-count footer printed on every non-machine-readable run: `ccs: searching N sessions (M empty, K hook) in <scope>`.
- `--help` restructured into named argument groups (Scope, Search mode, Filter, Output, Performance) with a five-example epilog.

**`ccst` enhancements:**
- `ccst hooks install` (zero-arg): auto-discovers bundled `config/hooks-bundle.json` and installs all six default hooks.
- `ccst hooks install --hook <name>`: install one named hook from the bundle.
- `ccst hooks uninstall [--hook <name>]`: remove matching hook entries from `~/.claude/settings.json`. Dry-run by default, `--apply` to write.
- `ccst skills uninstall [--skill <name>]`: remove skill symlinks. Refuses to remove non-symlinks unless `--force`. Dry-run by default.
- `ccst doctor`: health check — PATH for all five CLIs, env vars, `~/.claude/settings.json` validity, hook registrations, skill symlinks, PyPI version drift. Exit 0 if clean, 1 if any WARN or FAIL. `--no-pypi` skips the network check.
- `ccst shell install`: appends a `ccl()` shell function to `~/.bashrc` and/or `~/.zshrc` between sentinel markers. Idempotent.
- `ccst shell uninstall`: removes the sentinel-bracketed block.
- `ccst telemetry trim --max-size N --max-age-days N`: explicit pruning of hook telemetry data.

**Bundled config:**
- `config/hooks-bundle.json`: canonical bundle of all six default hooks (session-tag, prompt-guard, bash-security-review, confirm-8digit, edit-write-audit, session-end).

**New skills:**
- `list-empty-sessions`: wraps `ccs --emptiness only`, reformats output with count summary and copy-pasteable follow-up commands.
- `delete-sessions`: permanently deletes sessions by explicit basename. Four pre-flight checks (basename format, existence, in-session guard, empty-only guard). Dry-run by default; `--execute` requires 8-digit confirmation. Deletes cc-sessions dir, JSONL transcript, .tag file, and optionally `~/.claude/tasks/<encoded>/`.

**Library:**
- `cc_session_tools.lib.sessions.is_empty_session()`: returns True if a session's JSONL transcript contains no user-typed messages.
- `cc_session_tools.lib.sessions.find_jsonl_for_session()`: locates the transcript JSONL for a given session directory.
- `cccs_hooks.telemetry.maybe_rotate()`: auto-rotates `fires.jsonl` when it exceeds 10 MB into numbered slots (`fires.jsonl.1/.2/.3`).
- `cccs_hooks.telemetry_trim`: new module exposing `main()` for the `ccst telemetry trim` subcommand.

**`ccl` shell function:**
- Installed by `ccst shell install --apply`. Wraps `ccs` for list-mode usage (`ccl`, `ccl --global`, `ccl --emptiness only`).

**Docs:**
- `docs/global-claude-md-bootstrap-prompt.md`: self-contained prompt for configuring a user's global `~/.claude/CLAUDE.md` with CCST-aware guidance and interactive 8-digit gate selection.
- `TODO.md`: tracks the `notify-user` skill follow-up (separate public repo + CCST integration).
- `CHANGELOG.md`: this file (retroactive, covers all releases).

### Changed

- README install/upgrade sequence collapsed: single `uv`-primary sequence covering package install, `ccst skills install`, `ccst hooks install`, `ccst shell install`, and `ccst doctor` verification. `pipx` documented as a one-line alternative.
- README "Bundled skills" section updated to cover all five bundled skills.
- README "Hook management CLI" section updated to describe all new subcommands (uninstall, doctor, shell, telemetry).
- README `ccs` table updated with `--emptiness` flag and list-mode documentation.
- README adds `ccl` to the CLIs table and the introductory paragraph.
- `skills/move-session/SKILL.md`: new "Design decisions" section explaining why historical references in WORKLOG / earlier messages are not rewritten (historical record integrity) and why the SKILL.md uses `~/.claude/skills/move-session/scripts/...` paths (the skill directory is a symlink into the installed source).
- Telemetry rotation scheme changed from weekly 512 KB gzip files (`fires.YYYY-WW.jsonl.gz`) to 10 MB numbered slots (`fires.jsonl.1/.2/.3`). Tools that pattern-matched `fires.*.jsonl.gz` need updating.
- Skill renamed from `claude-usage` to `analyse-cc-usage` to match verb-first naming convention.

### Fixed

- Remaining personal identifiers scrubbed from `docs/superpowers/plans/` and any files introduced by parallel streams.

## [0.10.1] - 2026-05-11

### Fixed

- Replace personal paths in `cccs_hooks.session_tag` docstring and tests; bump to 0.10.1.

## [0.10.0] - 2026-05-11

### Added

- `cccs_hooks.session_tag`: new SessionStart hook that writes `<uuid>.tag` files, giving `claude-code-usage` a persistent mapping from session UUID to the `ccd` name tag.
- `ccst skills install` subcommand: symlinks all bundled skills into `~/.claude/skills/`. Dry-run by default, `--apply` to write, `--force` to replace wrong-target symlinks.
- `ccr --include-orphans`: also consider sessions whose `cc-sessions/` directory is missing (resume by transcript UUID only).

### Changed

- macOS added to CI test and install-check jobs.

## [0.9.0] - 2026-05-11

### Added

- `ccst skills install` subcommand (initial version, later extended in 0.10.0).
- macOS CI coverage.

### Fixed

- Remove `cccs` dependency from `ccst hooks install`.
- Remove `.resolve()` from `transcript_dir_for_project` to fix path handling on macOS.

## [0.8.0] - 2026-05-10

### Added

- `ccst` umbrella CLI with `hooks install` and `hooks run <name>` subcommands.
- `cccs_hooks` Python package (moved from a separate repository): `telemetry`, `transcript`, `confirm_8digit`, `cache`, `bash_security_review`, `edit_write_audit`, `prompt_guard`, `session_end` modules.
- `ccst --version` flag.

### Fixed

- Drop Python 3.10 from CI matrix (minimum supported version is now 3.11).

## [0.7.0] - 2026-05-10

### Added

- `--debug` flag and `CCX_DEBUG` environment variable for verbose output in `ccs`, `ccr`, and `ccd`.
- `ccs`: interactive 1-9/0 picker for ≤10 results with automatic exec into `ccr`.
- `ccs`: OSC 8 terminal hyperlinks on session basenames.
- `ccs`: "did you mean?" suggestion on zero results.
- `ccs`: `CCS_DEFAULT_GLOBAL` env var and `--local` override flag.
- `ccs`: `--json` and `--null` machine-readable output flags.
- `ccs`: `--since`, `--before`, `--days` date-range filters.
- `ccs`: `--exclude-hooks` flag to filter hook-security-check sessions.
- `ccs`: include transcript JSONL files in `--contents` search.
- `ccs`: batched `rg` calls with iterative ETA estimate.
- `ccr`: interactive 1-9/0 picker for 2-10 matching sessions.
- `ccr`: exact-match fast-path that skips enumeration for full basenames.
- `ccr`: fail-fast with clear message when `claude` is not on `$PATH`.
- `ccr`: validate and pass through recognised `claude` flags.
- `lib/claude_flags.py`: runtime enumeration of recognised `claude` CLI flags.
- `lib/picker.py`: shared 1-9/0 session picker used by `ccs` and `ccr`.
- `lib/debug.py`: `CCX_DEBUG` env-var support shared across CLIs.
- CI: release workflow — build, GitHub Release, PyPI OIDC publish.
- CI: `uv`-based build, Python 3.13 support, `install-check` job.

### Fixed

- CCX_DEBUG env var no longer leaks across invocations.
- Context deduplication in `ccs` search results.
- Picker sort order.
- CI fragility fixes.

## [0.6.0] - 2026-05-10

### Added

- `[dev]` extras group with `pytest`; version bumped to 0.6.0.
- Python 3.13 classifier.
- `lib/sessions.transcript_dir_for_project()`.

## [0.5.x] - 2026-05-10

### Added

- `claude-code-usage` CLI and `analyse-cc-usage` skill imported from an external repository.
- `--exclude-hooks` flag on the `query` subcommand.
- Session metadata parsing: `is_sidechain` and `initiation_type` columns; `parse_session_metadata()`.
- `load_jsonl_titles()` and cache update for session names.
- Persistent Parquet cache with `MANIFEST_VERSION 3`.

## [0.4.x] - 2026-05-09

### Added

- `find-claude-code-session` and `move-session` skills imported into the repository.
- `RootsConfigError`: explicit errors when roots env vars are missing or invalid.
- `docs/design.md`: architecture overview and env-var contract.

### Fixed

- Tighter threshold for sibling-project suppression guard in `ccd` typo prompts.
- `requires-python` reverted to `>=3.10` (later raised again).

## [0.3.x and earlier] - 2026-05-09

### Added

- Initial public release of `ccd`, `ccr`, `ccs` CLIs.
- `CLAUDE_SESSION_TOOLS_REPO_ROOT` and `CLAUDE_SESSION_TOOLS_PROJ_ROOT` environment variables replacing file-based roots config.
- Levenshtein typo protection for `ccd` under the strict root.
- `lib/rules.py`, `lib/roots.py`, `lib/sessions.py`, `lib/prompts.py`, `lib/tasklist.py`.
- `--version` flag on all three CLIs.
- `.gitignore` entry for `.worktrees/`.

[Unreleased]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.15.1...HEAD
[0.15.1]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.5.0...v0.6.0
