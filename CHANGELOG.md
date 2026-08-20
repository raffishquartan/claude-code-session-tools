# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.7.1] - 2026-08-20

### Fixed

- **`ccst pdata init --write`**'s backup-retry backoff is no longer a fixed-length lookup
  tuple indexed by attempt number, which would have silently broken with an `IndexError` if
  the retry-count constant were ever changed without a matching edit elsewhere. Backoff is
  now a formula keyed only off the attempt number.
- Each failed backup attempt is now reported through `--write`'s progress stream and log file
  before its retry, instead of only the final outcome (success or a single failure message)
  being visible.

### Changed

- The `.db` snapshot inside `--write`'s pre-cutover backup archive now shares its
  WAL-safe-copy implementation with the existing `lib/db.py` `backup_to()` helper (previously
  duplicated) - a correctness improvement that also applies to `ccst repair-sessions`'s own
  backup step, which no longer takes any connection on the live store it's backing up.

## [2.7.0] - 2026-08-20

### Fixed

- **`ccst pdata init --write`** no longer crashes with a raw traceback if its pre-cutover
  backup step hits an I/O error (e.g. a transient failure reading a network- or
  DrvFS-backed project root) or a locked/errored `.db` file. The failure is now retried a
  bounded number of times, and if it still fails, every row inserted during that run is
  rolled back and reported as a structured failure - matching the existing
  verification-failure behaviour - instead of leaving a half-completed migration (DB rows
  committed, cutover never run) with no non-crash signal.
- The backup tar.gz is now written to a temp path and atomically renamed to its final name
  only on full success, so an interrupted backup can never leave a corrupt file sitting at
  the filename a valid backup would use.

### Added

- **`ccst pdata init --write`** now backs up the project's `.db` (via the sqlite3 backup API,
  for a point-in-time consistent snapshot even under WAL mode) alongside the existing
  `project_root` tar, inside the same archive, before cutover touches anything.
- **`ccst pdata init --write`** streams phase-by-phase progress to stdout (import / verify /
  backup / cutover, including on failure/rollback) instead of producing no output until it
  finishes or crashes, and writes everything printed - plus the traceback of anything that
  still escapes unhandled - to a new `ccst-pdata-init-write.log` file inside the project,
  flushed after every line.

## [2.6.0] - 2026-08-18

### Added

- **`ccst hooks run context-window-warning`** and **`ccst context-override [on|off|status]`** —
  the context-window Stop-hook nudge and its override skill, previously owned by
  `claude-code-config-sync`, now live natively in CCST with identical behaviour (same 150k/200k
  thresholds, same message wording). The override flag moved from a
  `~/.claude/context-overrides/<session_id>` file to a `context_overrides` row in the shared
  `sessions.db`.

## [2.5.0] - 2026-08-17

### Changed

- **`ccst` now syncs its own install config automatically instead of nudging you to.** After an
  upgrade, the next non-exempt `ccst` command runs `install-everything --apply` itself, prints two
  lines to stderr saying it did, and then runs the command you asked for. 2.4.0's nudge-and-block
  gate is gone: it was guarded by `sys.stderr.isatty()`, and neither Claude Code's Bash tool nor a
  scheduled `ccsched` job has a TTY, so it never fired for the automated workflows that motivated
  it — and a nudge was the wrong remedy anyway, since what goes stale on an upgrade is
  *registration* (a new hook isn't in `settings.json` until `install-everything` runs) and that is
  one idempotent command the tool can run itself. `is_interactive` no longer gates anything
  anywhere.
- The auto-sync never writes to stdout and never changes the invocation's exit code. `--json`
  output stays machine-readable, and `ccsched`'s failure ledger can't mistake an install problem
  for a job failure.
- **`ccst doctor`'s `install:synced` WARN** now says the config will be applied automatically on
  the next `ccst` command, rather than telling you to run it.
- `install-everything.sh` runs `ccst install-everything --apply` instead of four separate partial
  install commands (which also means it now registers the bundled scheduled jobs, which it was
  silently skipping). README's recommended flows do the same; the five partial `install`
  subcommands are unchanged and still documented, now marked as advanced use for custom
  `--target`/`--source`/`--hook` and single-category dry runs.

### Added

- **`CCST_NO_AUTO_SYNC=1`** disables the automatic sync entirely — for CI, for bisecting, and for
  any environment where `~/.claude` must not be touched.
- **Failure backoff.** A sync that fails does so persistently (a real `~/.claude/skills/<name>`
  directory where a symlink belongs, unbalanced `CLAUDE.md` sentinels), so the version, timestamp
  and rc are recorded and it backs off for six hours rather than reprinting a failing five-step
  install on every command. Installing a different version resets it. The record is cleared by any
  successful sync, including an explicit `ccst install-everything --apply`.
- **`ccst doctor` FAILs `install:synced`** when an automatic sync has already tried and failed for
  the installed version, naming the rc and timestamp. That state is not self-recoverable, which is
  this check's own WARN/FAIL criterion.
- An exclusive, try-once apply lock (`~/.local/share/claude/.install-sync.lock`) so a scheduled job
  and an interactive command can't apply concurrently. A contender skips rather than waiting — the
  winner is applying the same thing.

### Exempt from the automatic sync

`ccst hooks run <verb>` (fires on every tool call in every open session, and must not rewrite
`settings.json` mid-session from inside a hook Claude Code invoked from `settings.json`),
`install-everything`, `doctor`, `repair`, `migrate`, any `install`/`uninstall` verb, and anything
run with `CCST_NO_AUTO_SYNC=1`. Machines where nobody ever types a `ccst` command still self-heal
within ~24h via the bundled daily `pdata-verify-all` scheduled job.

## [2.4.0] - 2026-08-15

### Added

- **`ccst` now nudges an interactive user when `install-everything --apply` hasn't been run for
  the currently-installed version.** `uv tool install --reinstall`/`--upgrade` (and `pip`/`pipx`
  equivalents) don't run any code after installing, so nothing previously told a user their
  skills/hooks/shell functions/scheduled jobs/`CLAUDE.md` config might be out of sync with a new
  version — the exact gap that let stale `~/.claude/skills/*` symlinks survive a package
  relocation undetected. `ccst install-everything --apply` now records the version it last
  succeeded for (a new `install_sync` table in `sessions.db`); any interactive `ccst` invocation
  (a real TTY on stderr) on a different version prints a nudge and exits, pointing at `ccst
  install-everything --apply`. Automated callers are never affected: `ccst hooks run <verb>` (the
  path Claude Code invokes on every tool call in every open session), every scheduled `ccsched`
  job, and any future non-interactive caller are exempt by construction, since none of them have a
  TTY. `ccst install-everything`, `ccst doctor`, `ccst repair`, and `ccst migrate` are also always
  exempt, so a user always has a way to see or fix the state this nudge is protecting against, even
  when the marker's own store (`sessions.db`) is corrupt.
- **`ccst doctor` reports install-sync state as a check (`install:synced`).** WARN, not FAIL —
  always self-recoverable with one command, same severity as the existing `ccsched-job:*` checks.
- `uv run <trusted-verb>` (e.g. `uv run pytest`, `uv run python -m ...`) now gets the same
  zero-review trust as the bare verb. `uv sync`/`build`/`lock` are now recognised as write-risk and
  cached after one real review, matching `npm install`/`cargo build`.

### Changed

- **`ccst doctor` now prints only WARN/FAIL results by default**, with a hint pointing at the new
  `--all` flag to see the full check list. A clean machine's `ccst doctor` output was otherwise
  dozens of `[OK]` lines a user had to scroll past to find the one thing that needed attention.
  `ccst doctor --all` reproduces the previous, always-print-everything behaviour. `--drift` mode
  (already WARN/FAIL-only, plus mute-aware) is unaffected.

### Fixed

- **`ccst doctor`/`repair`/`install-everything`'s health check could traceback on a corrupt
  `sessions.db` instead of reporting it cleanly.** `sqlite3.connect()` opens lazily and only fails
  once a query actually touches the file, so `check_sessions_project_dir_absolute` (reached by
  both `ccst doctor` and `install-everything`'s trailing health check), `_cmd_repair_sessions`
  (both dry-run and its `--execute` backup step), and `doctor_mutes.load_mutes` (`--list-mutes`/
  `--drift`) all still tracebacked on a corrupt store even after the initial `record_synced()` fix
  — undermining the exact recovery path (`ccst repair sessions`) this release's install-sync
  nudge depends on. All four sites now catch the corruption and report it as a normal check
  result/CLI error instead of an unhandled exception. Add/remove-mute (the write path for
  `--mute`/`--unmute`) is intentionally not covered — a raw traceback there is a UX-quality gap,
  not a correctness one, since there's nothing to preserve on a definitely-corrupt file.
- **`bash-security-review` let short, unpiped, write-risk commands bypass review entirely.** A
  bare `rm -rf ...` (or any other write-risk command with no shell composition and under the
  120-character trivial-allowlist length threshold) previously fell through to Tier 0.5's
  read-only pre-filter unreviewed, because `has_write_risk()` was only consulted for commands with
  pipe/redirect composition or over the length threshold. It's now consulted for every command
  that reaches Tier 0.5, regardless of shell composition or length. Several `_HEURISTIC_PATTERNS`
  entries also matched inside unrelated longer words — `sync`/`rsync` inside `nc`, `somebase64`,
  `printenvironment`, `newwget` — forcing unnecessary reviews on harmless commands; those patterns
  are now word-boundaried. The `id_rsa`/`id_ed25519` credentials-path pattern also briefly grew a
  matching trailing `\b` in the same sweep intended to exclude `myid_rsa_backup.txt`-style false
  positives; that trailing boundary was removed again since it silently stopped matching the
  mainstream `id_rsa_<host>`/`id_ed25519_<purpose>` suffixed key-naming convention — a real
  coverage regression, not a false-positive fix (the leading `\b` alone already excluded the
  intended false positive).

## [2.3.0] - 2026-08-14

### Fixed

- **A `pip`/`uv tool install`-from-PyPI install couldn't find its own bundled skills or
  `hooks-bundle.json`.** `skills/` and `config/` lived at the repo root, outside the installable
  `cc_session_tools` package, so `setuptools` had no way to include them in the wheel; `ccst.py`'s
  `_discover_source_dir()`/`_discover_bundle()` masked this by falling back to a hardcoded
  `~/repos/claude-code-session-tools/...` path that only ever worked on one machine, with one
  specific checkout location. Relocated `skills/` and `config/` under `src/cc_session_tools/` so
  they're packaged the same way for an editable checkout and an installed wheel, and rewrote the
  discovery functions to resolve them via a package-relative path instead of a fallback that would
  just mask a broken install. Verified end-to-end: built a real wheel, installed it into a clean
  venv with `HOME` pointed at a nonexistent directory, and ran `ccst skills install --apply` for
  real. Along the way, also found and fixed a second, compounding bug: `setuptools`'s package
  auto-discovery silently treated every `skills/<name>/` subdirectory as its own importable
  sub-package, which meant a separate `exclude-package-data` rule (intended to strip each skill's
  dev-only `tests/` directory and `__pycache__` from the wheel) was matching against the wrong
  package and doing nothing. Excluding `skills/`/`config/` from package discovery fixed both at
  once: a wheel's `skills/` payload dropped from 82 entries (41 of them test files, 28 bytecode
  cache) to the 30 files a skill actually needs at runtime.
- **`bash-hard-deny` blocked a script's own tempfile self-cleanup as a destructive operation.** The
  script-file check (and its inline-script/heredoc siblings) flagged any `os.remove`/`os.unlink`/
  `shutil.rmtree` call anywhere in an invoked script, including a `finally:`-block cleanup of a
  file the same script had just created via `tempfile.mkstemp`/`mkdtemp`/`NamedTemporaryFile` — the
  standard create-then-clean-up idiom. This made every skill using that idiom (e.g.
  `gmail-email-to-pdf`'s PDF renderer) permanently unusable. The three delete-detection call sites
  now carve out a narrow exemption: a delete call is not flagged if its sole argument is a bare
  variable assigned, earlier in the same content, directly from one of those three
  tempfile-creation calls. A literal path, an attribute access, an untracked variable, pathlib's
  delete-method form, and the Node `fs.*Sync` forms are unaffected and still always flagged.
- **The `ccsched` catch-up digest and inter-session message digest were invisible to the user.**
  Both hooks only emitted `hookSpecificOutput.additionalContext` — injected into Claude's own
  context, but never rendered outside the verbose transcript — so job findings and delivered
  messages went unnoticed. Both now also emit `systemMessage`; `SessionStart`'s catch-up digest
  always emits one (including an explicit "no activity" line when empty), `UserPromptSubmit`'s
  only emits one when there's real content, since it fires on every prompt.

## [2.2.0] - 2026-08-13

### Fixed

- **`ccl`/`ccs --global` silently dropped and under-counted recent sessions.** A relative
  `CLD_SESSION_DIR` could cause `sessions.db` writers to store `project_dir='.'`, which the
  `--global` root filter then silently excluded — and for `--global --limit N`, those excluded
  rows occupied slots in the DB-side `LIMIT N` window ahead of legitimate rows, so fewer than `N`
  results came back even when `N` valid ones existed. `sessions_db.py`'s three writers now reject
  non-absolute `project_dir` at the write boundary, printing a clear stderr diagnostic every time
  instead of failing silently; the `session-tag`/`after-response` hooks skip the write and
  surface the anomaly via their actual visible-output channel (SessionStart's `systemMessage` +
  `additionalContext`, Stop's `systemMessage`) rather than stderr, which a Claude Code hook
  discards on exit 0 outside `--debug` mode; and `ccs.py`'s `--global --limit` path grows its
  DB-side fetch window instead of fetching exactly `N` once, and now warns on stderr whenever a
  corrupted row is found and omitted from a listing.
- **`ccr <fragment>` gave a plain "no sessions match" for a corrupted row, with no pointer to the
  fix.** A `sessions.db` row with a non-absolute `project_dir` is invisible to every `ccr` lookup
  path — the exact-match fast path and `find_matching_sessions` both filter by root, and
  `find_orphan_transcripts` skips it too because its `cc-sessions/<name>/` directory already
  exists on disk. `ccr` now warns on stderr whenever a fragment matches such a row, whether or
  not it also matches a resumable session, and points at `ccst repair sessions`.

### Added

- `ccst repair sessions [--dry-run|--execute]` — a new top-level `repair` command family
  (parallel to the existing `migrate` family) — resolves and fixes sessions.db rows with a
  non-absolute `project_dir` by locating their on-disk `cc-sessions/` directory. Backs up
  `sessions.db` via `db.backup_to()` before `--execute` writes.
- `ccst doctor` now WARNs when any `sessions.db` row has a non-absolute `project_dir`.

## [2.1.1] - 2026-08-06

### Fixed

- **`move-session`'s SessionStart hook no longer fails on macOS's default
  bash.** `sessionstart-pending-rename.sh` used `mapfile`, a bash 4.0+
  builtin, but its `#!/bin/bash` shebang resolves to `/bin/bash` when Claude
  Code invokes it by absolute path — and on macOS that is the system-shipped
  bash 3.2.57 (Apple has frozen it there since the GPLv3 license change).
  Every session start printed `mapfile: command not found` and, under
  `set -euo pipefail`, silently stopped surfacing any real pending-rename
  markers. Replaced with a portable `while read` loop that works on bash
  3.2+.

## [2.1.0] - 2026-08-06

### Added

- **`CCST_SCREENSHOT_DIR` may now list several directories**, separated by the
  platform path separator (`:` on macOS/Linux/WSL, `;` on native Windows); the
  first one that exists wins. This is for setups that sync `settings.json`
  verbatim across machines, where the screenshot directory necessarily differs
  per machine (e.g. `~/Desktop` on macOS vs. `/mnt/c/Users/<user>/.../Screenshots`
  under WSL). A single path keeps behaving exactly as before, including when it
  does not exist.

## [2.0.0] - 2026-08-05

Two independent bugs, both surfaced by one 0.18.0 -> 1.4.1 upgrade: a stranded
settings.json that wedged every session, and a telemetry migration that could
not complete on the normal upgrade path.

### Removed

- **`ccst migrate telemetry --force`.** The flag existed to let the import run
  against a destination DB that already had rows, and its abort message told
  operators to reach for it. Both were wrong. `ccst migrate all` never accepted
  a `--force` (so `ccst migrate all --force` failed in argparse, giving no way
  to act on the advice at all), and for the case operators actually hit —
  post-install hook rows — `--force` was unnecessary, while for the case it was
  written for — a genuine re-run after a partial import — it double-inserted.
  The import now appends unconditionally and refuses a second run on an
  explicit marker instead, so there is nothing left for the flag to do. This is
  the only breaking change and it is why this is a major bump.

### Fixed

- **Hooks removed from CCST stayed registered in `settings.json` forever, and
  wedged the session.** `ccst hooks install` only ever added entries; nothing
  removed one whose hook no longer existed, and nothing else rewrote the file.
  A settings.json written at 0.16.0 therefore still carried `edit-write-audit`,
  `prompt-guard` and `session-end` — deleted or renamed in 0.17.0 — through
  every later upgrade. Because `ccst hooks run` validated the name with
  argparse `choices=`, an unknown name exited **2**, which is Claude Code's
  *blocking* exit code: the stale `prompt-guard` (UserPromptSubmit) swallowed
  every prompt before Claude saw it, and the stale `session-end` (Stop) stopped
  the session from ever ending. Three fixes, deepest first:
  - `ccst hooks run <unknown>` now exits **1** (non-blocking) with a message
    naming the valid hooks and the `ccst hooks uninstall --hook <name> --apply`
    that clears the stale entry. No future hook removal can wedge a session,
    whether or not the prune below has run.
  - `ccst hooks install` now prunes entries naming a hook this build cannot
    dispatch, in the same pass that adds new ones — so a rename drops the dead
    entry and adds its replacement together, rather than leaving both. Entries
    not spelled `ccst hooks run <name>` are never touched. `ccst
    install-everything` inherits this.
  - `ccst doctor` gained `hooks:no-stale`, which **FAILs** for each such entry.
    The existing hook check only looked bundle -> settings, so it reported a
    settings.json full of dead entries as entirely healthy.
- **`ccst migrate telemetry` refused to run on any machine that had opened a
  Claude Code session since installing CCST.** The import aborted if
  `telemetry.db` already had rows — but the hook writer fills that database
  from the first session after install, so the guard fired on the normal
  upgrade path rather than on the rare partial-run it was written for. The
  import now appends alongside the existing rows, and "has this already been
  imported?" is answered by an explicit marker in a new `migrations` table
  rather than by counting rows. Row count could never answer it: a non-empty
  table means the hook writer has been running, which says nothing about
  whether `fires.jsonl` was ever imported.
- **`ccst doctor` reported the telemetry migration as already done when it had
  not run.** Same root cause, opposite symptom: with `fires.jsonl` present and
  `telemetry.db` non-empty, the row-count heuristic downgraded FAIL to WARN,
  and since the SessionStart `pending-migration` hook only surfaces FAILs, the
  operator was never told the import was still outstanding. The telemetry check
  now reads the marker. The other three stores still use row counts and have
  the same latent flaw; tracked in TODO.md.
- **`ccst telemetry` listed the oldest rows in the store as its newest** after
  an import. Appended rows are older by `ts` but get the highest ids, and the
  query ordered by `id DESC`. It now orders by `ts DESC, id DESC` (id remains
  the tie-break — `ts` has whole-second resolution, so bursts of fires do tie).
- **Imported catch-up history would have resurfaced as new scheduler
  activity.** `catchup_events.id` is the per-session surfacing cursor
  (`WHERE id > ?`), so appending historical rows above every cursor's watermark
  would replay old catch-up digests and re-reap jobs that already ran. Every
  cursor is now advanced past the imported rows at the end of the migration
  (`cursor.advance_all_cursors_to`), applying the rule `seed_new_session`
  already applies to a new session: pre-existing history is not news. This
  replaces the old `id == N` alignment with the pre-1.0.0 row-count cursor
  files, which appending necessarily breaks.
- **The telemetry import's verification could fail on a correct migration.** It
  compared a before/after `COUNT(*)` delta, which a concurrent hook fire
  inflates — and this database always has live writers by the time anyone
  migrates. It now uses `sqlite3.Connection.total_changes`, which counts only
  the migration's own writes.

### Changed

- `HOOK_VERBS` / `HOOK_DESCRIPTIONS` moved from `cli.ccst` to
  `lib.hook_registry`, the single source of truth for which hooks this build
  can dispatch. Three consumers now need to agree on that set (the dispatcher,
  the settings.json prune, and the doctor check), which is one more than a CLI
  module should be the home for.
- `lib.db` gained `MIGRATIONS_DDL`, `migration_applied()` and
  `record_migration()` so any store can record a one-shot migration explicitly
  instead of inferring it from row counts.

## [1.4.1] - 2026-08-02

### Fixed

- **The bundled `pdata-verify-all` ccsched job auto-suspended on any machine that hadn't yet
  adopted pdata.** `ccst pdata verify --all-projects` correctly exits 2 for "zero project `.db`
  files found" (plan Decision 8, `2026-07-30-ccst-pdata-verify-and-skills.md`) — a deliberate,
  distinct-from-clean result for interactive callers. But the daily install-time job has no way
  to have adopted pdata before it exists, so on a fresh install this "nothing to verify yet"
  result recurred every day and, with `success_exit_codes` defaulting to `(0,)` for every
  bundled job, counted as 10 consecutive crashes — auto-suspending the job before it ever got a
  chance to run once a project actually had a store. `BundledJob` gained a `success_exit_codes`
  field (defaulting to `(0,)`, unchanged for every other bundled job) and `pdata-verify-all` is
  now registered with `(0, 2)`; a real per-project issue still exits 1, which isn't in that set,
  so it still counts as a failure. Only affects newly-registered installs — an already-suspended
  `pdata-verify-all` on an existing machine needs `ccsched edit pdata-verify-all
  --success-exit-codes 0,2` followed by `ccsched enable pdata-verify-all`, since
  `ccst ccsched-jobs install` never touches an already-registered job id.

## [1.3.1] - 2026-08-02

### Added

- **`ccsched add`/`edit --success-exit-codes`.** A job's exit code contract can now say
  "these codes mean I ran fine, not that I crashed" (default: `0` only, unchanged for every
  existing job). A check-style command like `ccst doctor --drift` legitimately exits 1 to mean
  "found something", but the scheduler previously had no way to distinguish that from a real
  crash, so 10 consecutive weekly "found drift" runs auto-suspended the job — see the paired
  `Fixed` entry below. `ccsched show` prints the configured codes.

- **`ccr` disambiguates duplicate transcripts sharing one session tag.** Hitting Ctrl-L twice
  mid-session leaves a cleared transcript alongside the original, both still tagged with the
  same session name — `ccr <tag>` previously resumed whichever the filesystem/DB scan happened
  to return first, silently, sometimes the blank one. `ccr` now detects when more than one
  JSONL transcript matches the resolved tag and prompts with a numbered picker showing each
  transcript's size and last-updated time; a non-interactive invocation (or more than 10
  candidates) resumes the most recently updated one and prints a warning instead of guessing
  silently.

### Fixed

- **CCST drift reports stopped appearing at session start after silently auto-suspending.**
  `ccst doctor --drift` (run weekly via a user-registered `ccsched` job) exits 1 whenever it
  finds unmuted drift — a documented, intentional signal for scheduled use, not an error. The
  scheduler had no way to tell "exited nonzero on purpose" from "crashed", so persistent
  (legitimate, unmuted) drift caused 10 consecutive "failures" and auto-suspended the job on
  2026-07-26, silencing it. Fixed via the new `success_exit_codes` job field (see `Added`
  above); a job's own crash/timeout accounting now checks against that set instead of a
  hardcoded `!= 0`. Separately, even a healthy run of this job never actually showed its
  findings — the session-start digest only ever rendered a bare `✓ ran <job>` checkmark for a
  successful run, with the drift report's own stdout discarded. A nonzero-but-configured-success
  exit now carries its stdout into the digest as a `⚠ <job> ran with findings:` block, and is
  never folded into the routine-backlog summary line the way ordinary runs are.

- **`ccl`/`ccs` help text no longer contradicts their own behaviour.** Three gaps, all
  reproducible from `ccl --help` alone: (1) `ccl`'s own help heredoc never mentioned `-n`/
  `--limit` even though it silently passes it through to `ccs` and it works; (2) the
  "`--limit` requires `--order-by opened or active`" constraint was documented only inside the
  `-n`/`--limit` help entry, invisible from the usage banner, the epilog examples, or `ccl
  --help`; (3) plain `ccs`/`ccl` (no `--global`) requires a `cc-sessions/` known to `sessions.db`
  under the current directory, silently, with no help text ever saying so or pointing at
  `--global` as the fix. `ccl --help` and `ccs`'s epilog now cover `--limit`'s constraint and
  the `--global` requirement; the "no cc-sessions/" error message itself now names `--global`
  as the fix. Also fixed a stale `docs/data-store-migration-steps.md` verify-step example that
  reproduced the exact `--limit` error from a real user report.

## [1.3.0] - 2026-08-02

### Added

- **`ccst pdata verify` — the integrity-check backstop.** `--project <name> [--full] | --all-projects`
  runs three checks per project: row-count parity against still-archived migration originals,
  `file_path` resolution, and suspiciously-close-in-time double-updates (spec §6.3) — results are
  persisted so `ccst doctor` can report a `pdata-verify:<project>` check cheaply, without doctor
  itself paying the cost of a verify pass. A `pdata-verify-all` `ccsched` job (daily@03:00) is
  provisioned automatically by `ccst install-everything`, feeding doctor rather than paging
  anyone directly.
- **`pm-pdata-schema-design` and `pm-pdata-conflict-resolution` skills.** The first is invoked
  before writing a genuinely new kind of structured data into a project's `ccst pdata` store
  (existing group vs. new group vs. extension table vs. free-text content); the second is invoked
  whenever `ccst pdata update`/`delete` exits 3 (a version conflict), presenting the current-vs-
  attempted diff for reconciliation rather than auto-retrying or silently picking a side.

  Note: `ccst pdata export` (spec §5's remaining `pdata` subcommand) is not designed or
  implemented by this work, nor by any prior `pdata` plan — flagged as the concrete scope for a
  future Plan E. See `docs/superpowers/plans/2026-07-30-ccst-pdata-verify-and-skills.md`.

- **Session-output index + `pm-update-central-files`.** `ccst pdata reconcile-session-output`
  backfills a per-project `session-output` record_group (on `ccst pdata`'s existing schema/CLI)
  from every `cc-sessions/*/out/` file on disk, incrementally via a per-project watermark.
  `ccst ccsched-jobs install` (wired into `ccst install-everything` as a 6th step) provisions a
  7-day job that runs it automatically; `ccst doctor` gets a matching health check. The
  `update-central-files` skill moves here from `claude-code-config-sync`, renamed
  `pm-update-central-files` (establishing the `pm-` prefix for cross-project,
  project-management-family skills), and gains an AUTO item that registers each session's `out/`
  deliverables into the index. See `docs/superpowers/plans/2026-07-30-ccst-pm-update-central-files.md`.

## [1.2.0] - 2026-07-31

### Added

- **`ccst pdata init` — unified per-project data-store init/migration.** New
  `--project <name> [--rehearse <path>] [--write]` verb (spec §7): a dry-run pass classifies
  every file in a project as folder-owned or db-owned (CSV/JSON get an automatic proposal; every
  other file defaults to folder-owned, pending human review), writes a hand-editable
  classification proposal, and — once approved and re-run with `--write` — imports the approved
  entries into that project's `ccst pdata` store, verifies the result, takes a full pre-cutover
  backup, and archives (never deletes) the original source files. A verification failure aborts
  before any file is touched, with every row inserted during that run soft-deleted. `--rehearse
  <path>` runs the whole procedure against a copy with zero effect on the real project or its
  `.db`. `ccst doctor` gains a check that WARNs about archived-but-undeleted migrated-source
  files (manual-delete-only, per spec). Ships with the `pm-project-init` skill, which drives the
  tool and applies the judgement its deliberately conservative classifier defers to a human. This
  is Plan B of the per-project data-store feature, built directly on Plan A's `ccst pdata`
  schema/CLI — `ccst pdata verify`, the `pm-pdata-schema-design`/`pm-pdata-conflict-resolution`
  skills, and any actual per-project migration content are deferred to later work (see
  `docs/superpowers/plans/2026-07-30-ccst-pdata-init-migration.md`'s Scope section).

## [1.1.0] - 2026-07-31

### Added

- **`ccst pdata` — per-project SQLite data store CLI.** New `records`/`schema` subcommands
  (`add`, `get`, `list`, `query`, `update`, `delete`, `restore`, `schema list`, `schema show`,
  `schema add-field`) operate on one SQLite `.db` per project under
  `~/.local/share/claude/project-db/<project>.db`. Every record lives in a `record_group`
  (validated lowercase-hyphenated name); an optional per-group extension table gives structured
  fields real typed/indexed columns without a CCST source change (`schema add-field`).
  `update`/`delete` use optimistic concurrency (`--version`) and surface a current-vs-attempted
  diff on conflict instead of silently overwriting or retrying. This is Plan A of the
  per-project data-store feature — `ccst pdata init`/migration, the `pm-`-prefixed skills, and
  `ccst pdata verify`/`export` are deferred to later plans (see
  `docs/superpowers/plans/2026-07-30-ccst-pdata-core.md`'s Scope section).

### Fixed

- **`ccst skills install --apply` no longer aborts on a stale symlink it manages.** A skill
  symlink under `~/.claude/skills/` left pointing at an old location (e.g. a since-deleted git
  worktree used for local dev/testing) was treated the same as a real user file at that path:
  both required `--force` or the install failed for that skill. `ccst install-everything --apply`
  calls the skills step with `force=False` and no way to override it, so any such stale symlink
  silently failed to (re)install while every other step kept going — `install-everything --apply`
  reported success even though the affected skill(s) were left broken. A dangling symlink under a
  directory this tool itself manages carries no user data, so it's now always safe to repoint on
  `--apply` without `--force`; `--force` is still required to move aside a real (non-symlink) file.

## [1.0.0] - 2026-07-25

0.19.0 (2026-07-14) shipped the data-store SQLite restructure below as a minor bump. In practice
it was breaking: upgrading silently left `ccmsg`, `ccsched`, and session-tag/mute data invisible
until a migration script was run by hand, `ccst doctor` reported that state as a harmless "not
yet created" WARN indistinguishable from a fresh install, and two of the four migration scripts
weren't part of the installed package at all — reachable only from a source checkout, not
`pip`/`uv tool install`. **0.19.0 was yanked from PyPI.** 1.0.0 supersedes it: same restructure,
plus the guard rail that should have shipped with it.

### Added

- **`ccst doctor` `migration-to-1.0.0:<store>` checks** (`ccmsg`/`ccsched`/`sessions`/`telemetry`) that
  distinguish "fresh install, nothing to migrate" (OK) from "upgraded from <1.0.0, legacy data
  still unmigrated" (FAIL) from "migration already ran, old files just not cleaned up yet" (WARN,
  no data at risk). The previous `data-store:<store>` check couldn't tell these apart — an empty
  new store read as "not yet created, expected before first use" whether or not there was live
  data waiting in the old location.
- **`ccst migrate ccmsg` / `ccst migrate telemetry` / `ccst migrate all`.** The `ccmsg` and
  telemetry migrations move from `scripts/migrate_ccmsg_to_db.py` /
  `scripts/migrate_fires_jsonl_to_telemetry_db.py` (dev-only, not packaged) into
  `cc_session_tools.cli.migrate_ccmsg` / `migrate_telemetry`, shipped as `ccst` subcommands like
  their `ccsched`/`sessions` siblings already were. `ccst migrate all` runs all four
  (sessions, ccmsg, ccsched, telemetry) in one pass.
- **`ccsched show <id>`.** Prints one job's full spec (cadence, coalesce, command, surface,
  catchup_window, timeout) and current state (registered_at, last_success, last_attempt,
  consecutive_failures, suspended, in_flight) — `ccsched list` only ever showed a summary row and
  `ccsched status` only shows ledger history, neither surfaced the full picture for one job.
- **`pending-migration` SessionStart hook.** Surfaces `migration-to-1.0.0:<store>` FAILs automatically at
  session start (WARN-only findings — migrated but not cleaned up — stay quiet, since no data is
  at risk). Honours `ccst doctor --mute <name>` so a deliberately-deferred migration doesn't
  renag every session. Cannot run the migration itself — see below.

### Changed

- **Major version bump, not minor.** An on-disk data-store relocation/reformat that can leave a
  user's data silently unmigrated is a breaking change regardless of whether the CLI surface
  changed; see `.claude/CLAUDE.md`'s version policy.
- `docs/data-store-migration-steps.md` updated for the `ccst migrate <store>` subcommands and the
  new doctor checks.

### Fixed

- **`ccsched list` column alignment.** Columns used fixed widths, so a job id or cadence longer
  than the assumed width threw every column after it out of alignment. Widths are now computed
  from the actual data on each run.

### Note on `ccst migrate all` / individual `ccst migrate <store>` commands

None of these can run automatically end-to-end. `ccmsg`, `ccsched`, and `telemetry` migration
each delete their own already-backed-up-and-verified old files as a final step, and the
`bash-hard-deny` PreToolUse hook statically blocks any script containing a delete call from
running inside a Claude Code session — by design, with no bypass. The `pending-migration` hook
above only detects and surfaces the gap; run `ccst migrate all` yourself from a plain terminal.

## [0.19.0] - 2026-07-14 (yanked from PyPI — see 1.0.0)

### Added

- **SQLite data-store migration.** Six flat-file/JSONL/TOML stores moved to
  SQLite (WAL mode) under the new `~/.local/share/claude/` root, each opened
  through a shared connection-setup helper (`cc_session_tools.lib.db.connect()`)
  instead of ad hoc pragma setup per module:
  - `ccmsg` → `ccmsg.db` — closes a retention/claim race (messages could be
    archived out from under an in-flight claim).
  - `ccsched` → `ccsched.db` — closes several registry/state races; per-row
    transactional writes replace whole-file read-modify-write.
  - Session-tag cache + `.last-opened`/`.last-active` activity sentinels +
    doctor-mutes → `sessions.db`. `ccl`/`ccr`/`ccs` session enumeration and
    "most recent N" matching now run one indexed `ORDER BY ... LIMIT` query
    instead of an O(roots × projects × sessions) filesystem walk; `ccs`/`ccr`
    gained a `--limit`/`-n` flag.
  - `~/.cache/claude/logs/fires.jsonl` (+ rotation) → `telemetry.db`. Fixes a
    pre-existing rotation/cursor-desync bug by switching the catch-up cursor
    to a monotonic row id. New `ccst telemetry query` command (filters on
    hook name, verdict, decision, time range).
  - `command-cache.db` and `claude-flags.json` relocated under the new root;
    `claude-flags.json`'s write is now atomic (previously a plain
    non-atomic write).
- **`ccst sessions migrate`** replaces the retired `ccst tags migrate`,
  migrating all three legacy session stores (tag cache, activity sentinels,
  doctor-mutes) into `sessions.db` in one non-destructive, dry-run-capable
  pass.
- **One-shot migration scripts** for each moved store
  (`scripts/migrate_ccmsg_to_db.py`, `scripts/migrate_fires_jsonl_to_telemetry_db.py`,
  `cc_session_tools.cli.migrate_ccsched`, `cc_session_tools.cli.migrate_sessions_db`), each
  following write → verify → tar-backup → (manual) delete — no store is
  auto-deleted by a migration script.
- **`ccst doctor`** gained a data-stores health check (`check_data_stores`)
  confirming all six stores can be opened.
- **`ccst gc report`** extractors for `ccmsg.db`, `ccsched.db`, and
  `sessions.db` replace the flat-file orphan-detection readers they
  superseded.
- **`select-agent-model` bundled skill.** Checked before every `Agent` tool
  dispatch to decide Sonnet-tier vs Opus-tier: Sonnet by default, Opus only
  for substantial design/ambiguity/cross-cutting reasoning or tricky-domain
  code work. The chosen tier is stated in the agent prompt's first line for
  an audit trail.
- **`do-executor-critic-assessor-loop` bundled skill.** A four-role
  orchestrator/executor/critic/assessor pattern for iterating a single
  candidate (document, design, code) through structured critique-and-revise
  rounds via sequential `Agent()` calls, for non-trivial work where quality
  matters more than speed. Documents the decision gate against the two
  other iteration options (single-shot dispatch; the `Workflow` tool's
  judge-panel pattern, which needs explicit user opt-in).

### Changed

- `~/.local/share/claude/<subsystem>.db` (SQLite, WAL mode) is now the
  standard location and format for any new Chris-added data store in this
  repo — see `.claude/CLAUDE.md`'s "Data store conventions" section.

### Fixed

- `lib/db.connect()`: the just-opened connection handle is now closed if
  pragma/DDL setup fails (previously leaked on a corrupt-file open).
- `lib/db.connect()`: the WAL-mode switch now retries on lock instead of
  immediately raising `SQLITE_BUSY` — SQLite does not honour `busy_timeout`
  for a journal-mode change, so cold-start concurrency (many processes
  creating the same fresh `.db` at once) could previously drop a connection.
- `lib/db.connect(readonly=True)`: the `file:` URI now URL-encodes the path,
  so a path containing `#`, `?`, or a space can no longer be misparsed.
- `bash-hard-deny`'s telemetry-log exfiltration guard now also blocks
  `cat`/`head`/`tail`/`hexdump`/`xxd`/`strings` reads of `telemetry.db`, not
  just `sqlite3` CLI reads (the cleartext columns were readable either way).
- `move-session` now keeps `sessions.db` in sync across a session
  move/rename (previously left a stale source row and no destination row,
  making the moved session briefly undiscoverable via `ccr`/`ccs`).

## [0.18.0] - 2026-07-11

### Added

- **`bash-hard-deny` PreToolUse hook.** Ported from claude-code-config-sync's
  `hooks/pre-tool-use/bash-hard-deny.sh`. A hard-deny gate for Bash commands: it
  categorically blocks destructive deletes (rm/rmdir/unlink/shred), delete-by-move
  into tmp-like locations, the same patterns inside inline python/node scripts,
  script files and heredoc bodies fed to interpreters, `gh api`/`gh release`
  DELETE calls, curl/wget mutating methods, `sudo`, `opentabs ... plugin_mark_reviewed`
  self-approval, and direct reads of the `fires.jsonl` telemetry log; everything
  else is auto-allowed. Runs via `ccst hooks run bash-hard-deny` (PreToolUse,
  matcher `Bash`) and ships in the hooks bundle. One bug fix vs the bash source:
  the fires.jsonl block now targets the real telemetry directory
  (`cccs_hooks.telemetry._DEFAULT_HOOKS_DIR`, `~/.cache/claude/logs`) instead of
  the stale `~/.claude/hooks` path; the `CCCS_FIRES_ACCESS=1` bypass is unchanged.
- **`update-command-cache` bundled skill.** Migrated from claude-code-config-sync;
  curates the SHA-256 command cache used by `bash-security-review`. Shares the
  `CCCS_FIRES_ACCESS=1` convention documented by the new `bash-hard-deny` hook (a
  discipline-maintained shared convention, not an enforced runtime dependency).
- **`reduce-persistent-context` bundled skill.** Measures the fixed per-session
  context footprint (CLAUDE.md files — global and project — skill descriptions,
  MCP tool names, hooks, harness baseline), ranks reduction candidates by
  token-saved-per-risk, and applies approved reductions behind 8-digit
  confirmation. Migrated from a previously unbacked `~/.claude/skills/`
  directory; no functional changes, just the move to
  `skills/reduce-persistent-context/{SKILL.md,scripts/,tests/}` and
  `ccst skills install` symlink deployment.

### Changed

- **`session-tag` hook now also emits the ccd/ccr SessionStart `additionalContext`
  message**, not just the `<session_id>.tag` file. Ported from
  claude-code-config-sync's `cc-wrapper-session-tag.sh`, which is being retired
  in favour of calling `ccst hooks run session-tag` directly. Mode-specific
  wording for `CLD_SESSION_MODE=new` vs `resume`; still a no-op when
  `CLD_SESSION_TAG` is unset (non-ccd/ccr sessions).

### Fixed

- **`ccst hooks run catchup` no longer replays full ledger history for a
  brand-new session.** A new session's cursor defaulted to offset `0`, so its
  first digest read the *entire* `fires.jsonl` history, including old,
  long-since-resolved failure streaks (e.g. 150+ stale `consecutive_failures`
  from a since-fixed job config). `cursor.seed_new_session()` now seeds a new
  session's cursor at the current end of the ledger before reconcile runs, so
  the first digest reflects only activity from that point forward. Wired into
  both the `catchup` hook and `ccsched sweep`'s fixed `cli-sweep` cursor.
- **`ccsched` jobs now auto-suspend after 10 consecutive failures instead of
  storm-retrying forever.** A misconfigured job (e.g. the `ccmsg-dead-letter-sweep`
  incident on 2026-06-27 — 153 consecutive failures over ~2h43m before a human
  noticed) had no backoff: `reconcile_and_launch()` relaunched it on every
  `SessionStart`/throttled `UserPromptSubmit` regardless of how many times it had
  already failed. The detached worker now flips a new `suspended` flag in
  `state.json` once `consecutive_failures` reaches 10, `reconcile_and_launch()`
  skips suspended jobs, and a one-time Telegram push (`notify.py`, direct Bot API
  call — no live session required) fires at the moment of suspension so a
  permanently broken job in a rarely-opened project doesn't go unnoticed. Run
  `ccsched enable <job>` after fixing the job to clear the suspension and resume.
- **`sessionstart-pending-rename` hook's cross-project cleanup command silently
  deleted nothing when `~/cc` is a symlink** (e.g. to an OneDrive sync target).
  GNU `find` does not descend through a directory symlink given as the search
  root unless `-L` is passed, so the printed
  `find ~/cc -name .pending-rename -delete` remedy cleared no markers while
  claiming to have cleared every one. Now prints `find -L ~/cc -name
  .pending-rename -delete`, with a regression test covering the symlinked-root
  case.

## [0.17.0] - 2026-07-09

### Added

- **Tier 0.5 read-only pre-filter in `bash-security-review`.** Piped read-only
  commands (`grep | wc`, `git log | head`, `find | sort`) were classed as
  nontrivial and escalated to Tier-3 LLM review despite no write/exec/network
  risk — 20-30 needless `claude` invocations per busy session. A `has_write_risk()`
  check (`_WRITE_RISK_RE` blocklist) now exits such commands immediately with
  verdict `read-only`; anything with redirection, `tee`, `rm`, `curl`, `ssh`,
  `sudo`, git write ops, or package managers still escalates.
- **`worklog-guard` hook** (`cccs_hooks.worklog_guard`) — PreCompact hook,
  matcher `manual` only, blocks `/compact` when the session's WORKLOG.md is
  stale. Replaces `after-response`'s deleted per-Stop WORKLOG nag: the check now
  fires once, at the moment un-persisted context is actually at risk, and blocks
  rather than warns. Only acts for `ccd`/`ccr` sessions with an existing
  WORKLOG.md; escape hatch `CCCS_ALLOW_STALE_WORKLOG=1`.
- **`ccst install-everything`** — runs all install steps (skills, hooks, shell,
  claude-md) then a `ccst doctor` health check. Dry-run by default; `--apply` to
  write, `--no-pypi` to skip the version-drift check. A first-class, idempotent
  CLI equivalent of the `install-everything.sh` bootstrap script.
- `ccst doctor` prints `Run: ccst install-everything --apply` when it reports any
  WARN or FAIL item.

### Changed

- **`session-end` hook renamed to `after-response`** (`cccs_hooks.session_end` →
  `cccs_hooks.after_response`). The old name implied "once per session", but the
  Stop event fires after every Claude response. Both its nagging checks are gone:
  the uncommitted-changes-on-a-feature-branch warning fired on every ordinary
  mid-task Stop, and the WORKLOG-staleness warning nagged one file ~5,000 times
  with no observed effect (a duplicate-registration bug also fired both twice per
  Stop). What remains is the `.last-active` sentinel touch that `ccs --order-by
  active` depends on.
- **`bash-security-review` pins its Tier-3 LLM escalation to a fixed model**
  (`--model sonnet`, override with `CCCS_REVIEW_MODEL`) instead of inheriting the
  invoking session's default `claude` model, whose cost and behaviour would
  otherwise drift silently.

### Fixed

- **`claude-code-usage --include-children` and the `children` command dropped
  parent sessions for agent subagents.** Subagent JSONL rows share the parent's
  `sessionId` (already billed under it), but `_fold_children` treated them as
  separate children and stripped the parent row from output entirely. It now
  distinguishes hook children (own UUID, separately billed — folded into
  `child_cost_usd`) from subagent children (shared UUID — parent stays visible,
  cost shown as a non-additive `agent_cost_usd` breakdown column), and adds
  `total_cost_usd = cost_usd + child_cost_usd`. Also fixes `sessions.py` to read
  the `customTitle` field.

### Removed

- **`edit-write-audit` hook** (`cccs_hooks.edit_write_audit`) — PostToolUse hook
  dropped with its bundle entry, dispatcher registration, and docs. Its three
  checks no longer earned their keep: the sensitive-path warning duplicated the
  pre-commit git-safety review; the out-of-repo-root check hardcoded `~/repos` as
  the only root (its OneDrive counterpart was stripped in a PII scrub and never
  replaced, so it fired on any work outside `~/repos`); and the WORKLOG.md
  auto-`git add` was a no-op wherever `cc-sessions/` is gitignored, which it
  always is under the convention.
- **`prompt-guard` hook** (`cccs_hooks.prompt_guard`) — UserPromptSubmit hook
  dropped with its bundle entry, dispatcher registration, and docs. It ran a
  credential/prompt-injection regex scan on every prompt in every session; a grep
  of every local transcript for its warning text found zero genuine firings ever.

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

[Unreleased]: https://github.com/raffishquartan/claude-code-session-tools/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v1.3.1...v2.0.0
[1.3.1]: https://github.com/raffishquartan/claude-code-session-tools/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.19.0...v1.0.0
[0.19.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/raffishquartan/claude-code-session-tools/compare/v0.15.1...v0.16.0
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
