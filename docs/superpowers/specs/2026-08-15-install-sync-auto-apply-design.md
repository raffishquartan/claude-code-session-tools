# Install-sync: auto-apply instead of nudge-and-block

Status: proposed, 2026-08-15, session `20260815-work-on-ccst`. Supersedes the nudge/block
mechanism shipped in 2.4.0 (one day old, never released beyond this machine's install).

## Problem

2.4.0 added a sync marker (`install_sync` table in `sessions.db`, written by
`ccst install-everything --apply`) and a gate in `ccst.main()`: on a version mismatch, print a
nudge and exit 1 instead of dispatching the requested command. The gate is guarded by
`is_interactive = sys.stderr.isatty()`.

Three things are wrong with it.

1. **The gate never fires for the case it was built for.** Claude Code's Bash tool allocates no
   TTY, so `sys.stderr.isatty()` is `False` for every `ccst` command Claude runs on the user's
   behalf. The nudge is invisible to exactly the automated-agent workflow that motivated it.
   (Verified: `python3 -c "import sys; print(sys.stderr.isatty())"` inside the Bash tool prints
   `False`.)
2. **Scheduled `ccsched` jobs are ungated for the same reason** — no TTY, different caller.
3. **A nudge is the wrong remedy anyway.** What actually goes stale on an upgrade is not code —
   `~/.claude/skills/*` symlinks and `ccst hooks run <verb>`'s dynamic import both resolve into
   `~/.local/share/uv/tools/cc-session-tools/lib/python3.13/site-packages/...`, a path
   `uv tool install --reinstall` reuses in place — it is *registration*. A hook or skill added in
   a newer version is not wired into `settings.json` / `~/.claude/skills/` until
   `install-everything` runs, so it silently does not run at all. That is a missing safety check,
   and the fix is one idempotent command the tool can run itself.

Wheels have no post-install hook (`pip`/`uv` run no arbitrary code after unpacking). That is a
hard constraint: nothing at install time can run `install-everything`. The first `ccst` process
to start after an upgrade is the earliest possible moment, and that is where this design puts it.

Chris's direction: *"Do auto apply. If someone has installed CCST they should just have
everything."*

## Non-goals

- Any attempt at a post-install wheel hook, or at making the packaging tool run CCST code.
- Auto-apply from the other seven console scripts (`ccd`, `ccr`, `ccs`, `ccmsg`, `ccsched`,
  `claude-code-usage`, `cccs-stats`). One entry point, one decision site — see §7.
- Auto-*upgrading the package itself*. `ccst doctor`'s PyPI drift check stays a WARN; installing a
  new version remains a deliberate human act. This design only syncs config to whatever version
  is already installed.
- Any change to `claude-code-config-sync` (CCCS). §9 documents the interaction; it is pre-existing.
- Removing any CLI subcommand — see §8.

## Design

### 1. `ensure_synced()` replaces `should_block_for_unsynced_install()`

`should_block_for_unsynced_install` is deleted outright, not deprecated (this repo's "deleting
code is a feature" standard; it has one production caller, `main()`, plus its tests).
`install_sync.py` gains a pure
decision function plus an impure executor, keeping the existing pure/impure split that made the
2.4.0 gate testable:

```python
def is_auto_sync_exempt(*, noun: str | None, verb: str | None, opted_out: bool) -> bool:
    """§2's exempt table. Pure, argv-and-env only — deliberately answerable
    without touching sessions.db, so an exempt caller never opens it."""

class AutoSyncAction(str, Enum):
    SKIP_SYNCED   = "skip-synced"    # synced_version == installed_version
    SKIP_BACKOFF  = "skip-backoff"   # a prior auto-apply for this version failed recently (§5)
    APPLY         = "apply"

def decide_auto_sync(
    *,
    installed_version: str,
    synced_version: str | None,
    last_failure: FailedAttempt | None,
    now: datetime,
) -> AutoSyncAction: ...
```

`FailedAttempt` is a frozen dataclass of `(version: str, at: datetime, rc: int)`.

The split into two pure functions is load-bearing, not stylistic: `is_auto_sync_exempt` must be
answerable from argv and the environment alone so that `ensure_synced` can short-circuit an
exempt caller (above all `hooks run`) **before** any marker read. `decide_auto_sync` is only ever
reached with markers already in hand.

`is_interactive` does not appear anywhere in the new signature. It was the bug: TTY-ness has no
bearing on whether the installed config should match the installed version. The interactive
human, Claude Code's Bash tool, and a `ccsched` job now all get identical behaviour.

`ensure_synced(...)` is the impure wrapper called by `main()`, in this order: evaluate
`is_auto_sync_exempt` and return immediately if exempt; otherwise read the markers, call
`decide_auto_sync`, and on `APPLY` take the lock (§3), run the apply (§4), and record the
outcome. It never raises to its caller and never refuses to dispatch — the "block" semantic is
gone completely.

`ensure_synced` lives in `install_sync.py` but needs `run_install_everything` from `cli/ccst.py`,
which already imports `install_sync`. Resolve with a function-local import inside `ensure_synced`,
matching the lazy-import convention `ccst.py` already uses throughout (`_cmd_doctor`,
`_cmd_install_everything`, `_cmd_ccsched_jobs_install` all import their dependencies inside the
function body). Do not invert the dependency by moving the five step functions out of `ccst.py` —
that is a much larger refactor with no benefit to this change.

### 2. Trigger point: `ccst.main()`, before dispatch, for every non-exempt invocation

`main()` calls `ensure_synced()` immediately after `parser.parse_args()` and after the existing
`args.noun is None` usage check — same call site as today's gate. Three consequences fall out of
that placement and are all wanted: an invalid command line still fails at argparse without
triggering an apply; `ccst --version` / `ccst --help` / bare `ccst` never trigger one (argparse
and the usage check both exit first); and a successful apply is in place *before* the requested
command runs.

The exempt set (skip the whole check, do not even read the marker):

| Rule | Exempt | Why |
|---|---|---|
| `noun == "hooks" and verb == "run"` | always | Fires on every tool call in every open session. Must never apply (§6), and must never pay for the check. |
| `noun in {install-everything, doctor, repair, migrate}` | always | Carried over unchanged from 2.4.0's `_EXEMPT_NOUNS`. `install-everything` would recurse; `doctor` must stay able to *report* the out-of-sync state rather than silently erasing it; `repair` and `migrate` are the recovery tools for a broken store and must run under any store state. |
| `verb in {"install", "uninstall"}` | always | **New.** `ccst skills install --target /tmp/x`, `ccst hooks uninstall --apply` etc. are the user driving install state by hand. Running a full default-target auto-apply underneath them is both surprising and self-contradictory. |
| `CCST_NO_AUTO_SYNC=1` in the environment | always | Escape hatch for CI, for bisecting, and — critically — for this repo's own test suite (see Testing). |

Everything else — `ccst pdata *`, `ccst sessions *`, `ccst telemetry *`, `ccst gc *` — is a
carrier.

**Cost of the check on a carrier invocation: 0.56 ms** (measured: `get_synced_version()`, one
readonly SQLite open/read/close) against a **94 ms** bare `ccst --version` process start. The
2.4.0 comment in `main()` that avoids "a SQLite open/close on every invocation" was protecting a
0.6% cost; it is retained only for `hooks run`, where the invocation count (per tool call, per
session) rather than the per-call cost is the reason.

### 3. Exclusive, non-blocking apply lock

Two processes can reach `APPLY` at once — e.g. a `ccsched` job at 03:00 and a Claude Bash call.
`write_json_atomic` makes `settings.json` writes atomic, so no file is ever corrupted, but a
concurrent read-modify-write can still lose an update.

An exclusive lock file guards the apply: `paths.data_home() / ".install-sync.lock"`
(`~/.local/share/claude/.install-sync.lock` by default, redirected by `CCST_DATA_HOME` so tests
can sandbox it), created with `os.open(O_CREAT|O_EXCL)`, holding the creator's pid, reclaimed if
the recorded pid is dead. This
is exactly `src/cc_session_tools/lib/scheduler/lock.py`'s mechanism. Per this repo's "one source
of truth" standard, the second use is the point at which it gets hoisted: extract a generic
`src/cc_session_tools/lib/proc_lock.py` (`exclusive_lock(path) -> contextmanager`, raising
`LockHeld`) and have `scheduler/lock.py` delegate to it, preserving its per-job path derivation,
its `InFlightLockHeld` exception type, and its in-process `threading.Lock` TOCTOU guard.

Contention behaviour is **try-once, do not wait**: a contender that finds a live holder skips the
apply silently and dispatches the requested command immediately. Blocking an unrelated CLI call
on a lock would make auto-apply a latency hazard, and skipping is harmless — the winner is
applying the same thing, and the loser's next invocation will see the marker already advanced.
This matches `scheduler/lock.py`'s own documented policy ("two sessions launching the same owed
job is harmless — only the lock winner runs").

### 4. What the apply actually runs, and what it prints

`_cmd_install_everything` is split: a parameterised core plus a thin argparse adapter.

```python
def run_install_everything(
    *,
    apply: bool,
    stream: TextIO,
    health_check: bool,
    no_pypi: bool = False,
    skills_target: str | None = None,
    hooks_target: str | None = None,
    fragments_dir: str | None = None,
    claude_md_target: str | None = None,
) -> int: ...
```

`_cmd_install_everything(args)` becomes the adapter (`stream=sys.stdout`, `health_check=True`).
Auto-apply calls `run_install_everything(apply=True, stream=<buffer>, health_check=False)` with
all four target overrides at their defaults.

Three properties of that call are load-bearing:

- **`health_check=False`.** Measured: the whole `ccst install-everything` dry run is **1.57 s**,
  of which the five install steps are **16.8 ms** (skills 2.4, hooks 1.2, shell 1.4, claude-md
  1.4, ccsched-jobs 10.3). The remaining ~1.55 s is the trailing `ccst doctor`, dominated by its
  PyPI network check. Auto-apply is not a diagnostic run and must not pay that, or take a network
  dependency, on someone else's command.
- **Default targets only.** Auto-apply never inherits the requested command's `--target`. A
  `ccst pdata get --db /tmp/x` invocation must not redirect where skills get symlinked. §2's
  `install`/`uninstall` verb exemption removes the only case where this would be confusing.
- **Never writes to stdout.** All auto-apply output goes to stderr. `ccst sessions list --json`
  emits machine-readable stdout, and `scheduler/worker.py` carries a job's `stdout` into the
  ledger as its recorded *findings* for a nonzero-but-successful exit (worker.py ~L105–110);
  interleaving install chatter there is a correctness bug, not a cosmetic one.

Accepted cost of routing to stderr: `worker.py` ~L90 also records the first 200 characters of a
*failed* job's stderr as its ledger error message. The first `ccsched` job to run after an
upgrade will therefore, if it also fails for its own unrelated reason, have the two sync lines
defined below prefixed onto its recorded error. That is once per version bump per machine, on an
entry that is already an error — a better trade than either suppressing the message (which
recreates 2.4.0's invisibility bug) or making it conditional on TTY (same bug, same mechanism).

Output on **success** is exactly two stderr lines:

```
ccst: install config is out of sync (installed 2.5.0, last synced 2.4.0) — syncing…
ccst: install config synced to 2.5.0.
```

(First line reads `never synced` in place of `last synced 2.4.0` when the marker is absent.)

The per-step detail is buffered and discarded on success. This answers "should there be a visible
print before it happens, at minimum?" — yes, and it is enough. A user who wants the detail has
`ccst install-everything` (still dry-run by default) to preview and `--apply` to see the full
five-step table. Rejected alternative: emitting the full step output whenever anything changed.
It needs a change-counting protocol the five step functions do not currently have (they print and
return an rc, with no structured "changed" signal), and the common case — one changed hook
registration — does not justify ~30 lines of table on an unrelated command.

Output on **failure** is the buffered step output, verbatim, to stderr, followed by §5's pointer.

### 5. Failure: never silent, never a retry storm, never fatal

The five steps have real failure modes. The two reachable without a corrupt store:
`_cmd_skills_install` returns 1 when `~/.claude/skills/<name>` exists as a real directory and
`--force` was not passed (auto-apply never passes `--force`, deliberately — it must not move a
user's real directory aside behind their back); `_cmd_claude_md_install` returns 1 on
`MalformedBlockError` from unbalanced sentinels in `~/.claude/CLAUDE.md`. Both are persistent:
they will fail identically on every retry until a human intervenes.

Today `_cmd_install_everything` proceeds through the remaining steps and simply never calls
`record_synced` when `overall_rc != 0`. Continuing through the remaining steps stays right — the
four healthy categories should still get synced. Silently never advancing the marker does **not**
stay right once this is an unasked-for side effect: it would reprint a failing five-step install
on every subsequent `ccst` invocation, forever.

So a failed auto-apply records three keys in the existing `install_sync` KV table (`(key, value,
updated_at)` — **no DDL change**, which is what keeps this a minor bump, §11):

- `last_attempt_version` — the installed version that was attempted
- `last_attempt_at` — ISO timestamp
- `last_attempt_rc` — the non-zero rc

`decide_auto_sync` returns `SKIP_BACKOFF` when `last_attempt_version == installed_version` and
`now - last_attempt_at < 6h`. During backoff the invocation prints **one** stderr line and
proceeds:

```
ccst: install config is out of sync (installed 2.5.0, last synced 2.4.0) and the last auto-sync
failed at 2026-08-15T09:12:04Z — run `ccst install-everything --apply` to see why.
```

A one-line warning on every invocation is the correct volume here: something is genuinely broken
and needs a human. After the window, one full retry is attempted (which re-arms the window if it
fails again). Installing a *different* version resets the backoff, because the key is
version-scoped — a new release may well fix the failing step.

The three keys are cleared on any successful `record_synced()`, including the one written by an
explicit `ccst install-everything --apply`.

**Auto-apply never changes the invocation's exit code.** `ccst pdata list` exits with
`_cmd_pdata_list`'s rc whether the auto-apply succeeded, failed, was skipped, or lost the lock.
Auto-apply is a side effect, not the command; conflating the two would break every caller that
checks `$?` — including `ccsched`'s success/failure ledger, which would start auto-suspending
healthy jobs after 10 consecutive "failures" that were really install-sync failures.

**Degenerate case: the marker store itself is unwritable.** All of these keys live in
`sessions.db` (path from `sessions_db.default_db_path()`, honouring `CCST_SESSIONS_DIR`). If that
file is corrupt, `get_synced_version()` already returns `None` (it catches
`sqlite3.DatabaseError` by design), so `decide_auto_sync` returns `APPLY`; the five steps run and
succeed; and `record_synced()` then raises — which `_cmd_install_everything` already catches
today, printing a warning that points at `ccst repair sessions`. The backoff keys cannot be
written either, for the same reason. The decided behaviour: keep that existing warning (routed to
stderr, like everything else in §4), set an in-process flag so a single `ccst` process never
retries, and **accept that every subsequent `ccst` process re-runs the 16.8 ms apply and re-prints
the warning until the store is repaired**. Backoff cannot help when the backoff store is the thing
that is broken, and inventing a second on-disk sentinel to work around one corrupt SQLite file
would violate this repo's one-store-per-subsystem rule. The noise is loud, bounded, accurate, and
`ccst repair sessions` is exempt (§2), so the fix is always reachable.

### 6. `hooks run` is exempt and self-heals via the scheduler, not via a hook

`ccst hooks run <verb>` neither applies nor reads the marker. Two independent reasons:

- **Hot path.** `bash-hard-deny`, `marker-allow`, `bash-security-review` and `confirm-8digit`
  fire on every tool call in every open session.
- **Wrong moment.** Rewriting `settings.json` from inside a hook that Claude Code invoked *from*
  `settings.json`, mid-session, is a race no atomic write makes tidy. Hooks already tolerate being
  a version behind for the life of a session; that is the status quo and it is acceptable.

The obvious alternative — a SessionStart hook that self-heals — **does not work, and the reason is
structural**: a new hook verb is not in `settings.json` until `install-everything` has run, which
is the very thing being fixed. It could not bootstrap itself on the upgrade that introduced it.
Overloading an already-registered verb (`session-tag`, `catchup`, `messaging-deliver`,
`pending-migration`) to carry an unrelated side effect avoids the bootstrap problem but couples
self-healing to one hook's continued existence, and puts a `settings.json` write inside session
startup. Rejected. (Note for anyone tracing the earlier analysis: the SessionStart hook that
prints `Config in sync.` is **not** in this repo — it is
`~/repos/claude-code-config-sync/hooks/session-start/check-config-drift.sh`, in CCCS.)

The guaranteed backstop is the scheduler. `bundled_jobs.BUNDLED_CCSCHED_JOBS` includes
`pdata-verify-all` at `daily@03:00`, whose command is `("ccst", "pdata", "verify",
"--all-projects")` — a fresh `ccst` subprocess, a non-exempt noun, therefore a carrier. Worst-case
self-heal latency on a machine where the human never types a `ccst` command and Claude never runs
one is **~24 hours, with zero user action**. Those jobs are themselves launched detached by the
`catchup` hook on SessionStart/UserPromptSubmit, so "the machine is being used at all" is
sufficient. This is why §2 can afford to leave the hot path completely alone.

### 7. Scope: `ccst` only

`ccd`/`ccr`/`ccs`/`ccmsg`/`ccsched`/`claude-code-usage`/`cccs-stats` do not get the check in v1.
One entry point means one decision site, one place to test, and one place a mistake can break —
and a mistake in `ccd`/`ccr` breaks session launch itself. The §6 scheduler backstop already
covers the "human only ever runs `ccl`" case within a day. Revisit only if that latency proves
too slow in practice.

### 8. Partial install commands: keep all five

**Recommendation: keep `ccst skills install`, `ccst hooks install`, `ccst shell install`,
`ccst claude-md install`, and `ccst ccsched-jobs install` as user-facing subcommands. Demote them
in the documentation; do not remove them.**

- They *are* the implementation. `run_install_everything` dispatches to the same five functions;
  removing the subcommands removes only the argparse entry, gaining nothing.
- They carry overrides `install-everything` cannot express per-category: `skills install
  --source/--target/--force`, `hooks install --source/--hook/--target`, `shell install
  --fragments-dir`, `claude-md install --target`. `hooks install --hook <name>` in particular
  installs a non-default hook that is deliberately absent from the bundle — a capability
  `hook_registry.py`'s own module docstring calls out as intentional.
- Single-category dry-run is the debugging tool of choice when one step of a five-step apply
  fails — precisely the §5 failure path.
- The `*-uninstall` counterparts are a distinct, clearly-needed concern (removing CCST's
  footprint) and stay regardless. `hooks uninstall` with no `hooks install` alongside it is
  incoherent surface.
- `_cmd_hooks_run`'s own stale-hook error message instructs the user to run
  `ccst hooks uninstall --hook <name> --apply` or `ccst hooks install --apply`. Removing them
  would require rewriting a user-facing recovery path for no gain.
- Removing a documented CLI subcommand is a **breaking change** under `.claude/CLAUDE.md`'s
  version policy, requiring a major bump. Spending 3.0.0 on deleting working surface that
  `install-everything` needs anyway is a bad trade.

What changes instead is emphasis. `README.md`'s recommended flows stop listing the five commands
and use `ccst install-everything --apply`; the five move under an explicitly-labelled advanced
section (see "Documents and docstrings to update"). `install-everything.sh` stops shelling out to
four of them individually.

### 9. Interaction with CCCS `settings.json` sync (pre-existing, now unattended)

`~/.claude/settings.json` is also managed by CCCS, whose `check-config-drift.sh` auto-applies
repo→local when the repo commit is newer (main branch only) and warns on local-ahead. CCST
writing hook registrations into that same file already creates local-ahead drift today; this is
not new. What *is* new is that it will now happen without a human having typed the command.

No change is proposed on either side. The interaction is bounded and already safe:
`prune_stale_hooks` only ever touches entries whose command starts with `ccst hooks run ` and
leaves every foreign entry — including all of CCCS's own `$HOME/repos/claude-code-config-sync/...`
hooks — untouched, and it only collapses containers it emptied itself. The correct follow-up, if
the drift warning becomes noisy, is a CCCS-side change, out of scope here.

### 10. Why applying without asking is safe

Auto-apply removes the user's chance to see a diff before `settings.json`, `CLAUDE.md`, and their
shell rc fragment are written. That is an acceptable trade here, and the argument is per-step, not
hand-waved:

| Step | Blast radius, verified in source |
|---|---|
| `_cmd_skills_install` | Creates symlinks only. Refuses (rc 1, no write) when the destination is a real file/dir and `--force` is absent — and auto-apply never passes `--force`. Repoints only symlinks it created. |
| `_cmd_hooks_install` | `prune_stale_hooks` removes only `ccst hooks run <name>` entries naming a verb this build no longer has; `merge_hook_settings` adds only bundle entries. Foreign entries untouched. Returns early with `Already up to date` and **no write** when there is nothing to add or remove. |
| `_cmd_shell_install` | Owns exactly one file, `~/.shellrc.d/ccl.sh`, whose header says "Managed by ccst — do not hand-edit". Returns `ALREADY_PRESENT` without writing when the content matches. Never touches `.bashrc`/`.zshrc`. |
| `_cmd_claude_md_install` | Rewrites only the text between its own sentinels. Returns `ALREADY_PRESENT` without writing when the block matches. Raises `MalformedBlockError` (→ rc 1, no write) rather than guessing on unbalanced markers. |
| `_cmd_ccsched_jobs_install` | Explicitly never modifies an existing job id, even a hand-edited one — membership check against `registry.load_registry()` before `add_job`. Only adds missing bundled jobs. |

Every step is a no-op-without-write when the config already matches, which is also why the
steady-state cost is 16.8 ms. The change being applied is, by construction, "make the config match
the version the user deliberately installed".

### 11. Version bump: 2.5.0 (minor)

Per `.claude/CLAUDE.md`'s policy:

- **Not major.** No on-disk data is relocated, reformatted, or made unreadable — the new failure
  keys are rows in the existing `install_sync` KV table, so there is no DDL change and no
  migration, hence no `ccst doctor` FAIL-until-migrated check is owed. No documented CLI
  subcommand or flag is removed (§8). `should_block_for_unsynced_install` is an internal library
  function with one in-repo caller, not public surface.
- **Not patch.** It is a deliberate behaviour change plus a new env var (`CCST_NO_AUTO_SYNC`).
- **Minor.** Backwards-compatible behaviour change: a mode that previously *refused* to run a
  command now runs it, having first fixed the reason it was refusing. The only observable
  regression risk is for a caller that depended on `ccst <cmd>` exiting 1 on a stale install — a
  behaviour that existed for one day, in 2.4.0, and was documented as a nudge for humans.

## Documents and docstrings to update

Repo docs:

- `README.md`
  - "Install and set up" / **"After upgrading"** callout (~L90–95): delete "`ccst` itself will
    remind you — any interactive command … prints a nudge and exits until you do". Replace with
    the auto-sync behaviour and the `ccst doctor` / `CCST_NO_AUTO_SYNC` escape hatches.
  - "Manual path" step 2 (~L103–107): replace the three individual `install --apply` lines with
    `ccst install-everything --apply`.
  - "Upgrade" step 2 (~L143–150): same replacement; note that step 2 is now optional because the
    next `ccst` command does it.
  - New subsection under "Installation": **"Automatic install sync"** — what triggers it, the two
    stderr lines, the exempt set, `CCST_NO_AUTO_SYNC=1`, the failure/backoff behaviour, and the
    daily-ccsched backstop.
  - `### ccst hooks install` (~L692), `### ccst skills install` (~L744), `### ccst shell install`
    (~L775), `### ccst claude-md install` (~L812): add a one-line "you normally do not need this;
    `install-everything` runs it — use this for a custom `--target`/`--source`/`--hook`" preamble.
    Do not delete.
  - `### ccst doctor` (~L832–840): update the `install:synced` sentence for the new WARN vs FAIL
    split.
- `CHANGELOG.md` — a new `## [2.5.0]` entry (new entry; 2.4.0's history is not edited). Changed:
  auto-apply replaces the nudge/block; `is_interactive` no longer gates anything. Added:
  `CCST_NO_AUTO_SYNC`, failure backoff, `install:synced` FAIL state.
- `install-everything.sh` — replace steps 2–5 (`ccst skills/hooks/shell/claude-md install --apply`,
  L69–84) with a single `ccst install-everything --apply`; renumber the `step "N/6"` labels; update
  the closing "For future upgrades, you only need:" line (L95) to drop
  `&& ccst install-everything --apply`.
- `.claude/CLAUDE.md` — "After merging a PR" section: note that `uv tool install --reinstall …` no
  longer needs a follow-up `install-everything`, the next `ccst` command syncs.
- `TODO.md` — no change needed (its only install references are about a future
  `skills install --from-git`, unaffected).

In-code docstrings and help text:

- `src/cc_session_tools/lib/install_sync.py` — module docstring (it currently says "lets `main()`
  nudge an interactive user"); `should_block_for_unsynced_install`'s docstring goes with the
  function; new docstrings for `decide_auto_sync`, `ensure_synced`, `record_failed_attempt`,
  `get_failed_attempt`.
- `src/cc_session_tools/cli/ccst.py` — `main()`'s comment block (L2228–2234, the "read the marker
  only when interactive" rationale) rewritten for the new exempt set and the measured costs;
  `_cmd_install_everything`'s docstring; the `install-everything` subparser `help=` (L2178–2181).
- `src/cc_session_tools/lib/doctor.py` — `check_install_everything_synced`'s docstring and both
  WARN `reason` strings ("run `ccst install-everything --apply`" → "will be applied automatically
  on the next `ccst` command; run `ccst install-everything --apply` to do it now"), plus the new
  FAIL branch.
- `src/cc_session_tools/lib/scheduler/lock.py` — module docstring, if §3's extraction lands.

## `ccst doctor` change

`check_install_everything_synced(installed_version, synced_version, failed_attempt)`:

- synced → `OK` (unchanged).
- out of sync, no failed attempt for this version → `WARN`, reason updated to say it will
  self-apply on the next non-exempt `ccst` command.
- out of sync **and** `failed_attempt.version == installed_version` → **`FAIL`**, naming the rc and
  timestamp. This state is not self-recoverable — auto-apply has already tried and will keep
  failing — which is exactly the WARN/FAIL criterion the existing docstring states.

`doctor` remains exempt from auto-applying, so it can always tell the truth about this state.

## Testing

`tests/conftest.py` gains `monkeypatch.setenv("CCST_NO_AUTO_SYNC", "1")` in the existing autouse
`_clean_session_root_env` fixture. **This is not optional.** Two groups of existing tests reach
`main()` and today rely on `is_interactive=False` to skip the gate:

- `tests/test_ccst_main_gate.py` calls `ccst.main()` in-process.
- Roughly fifteen modules (`test_ccst_cli.py`, `test_ccst_doctor.py`, `test_ccst_pdata_cli.py`,
  `test_ccst_install_everything.py`, `test_ccst_shell_install.py`, …) shell out via
  `subprocess.run([sys.executable, "-m", "cc_session_tools.cli.ccst", …])`.

Without the opt-out, both groups would run a real five-step install against the developer's live
`~/.claude` on every test run. `monkeypatch.setenv` mutates `os.environ`, which the subprocess
group inherits, so the one autouse fixture covers both. Tests that exercise auto-sync delete the
var explicitly and redirect `CCST_SESSIONS_DIR`, `CCST_DATA_HOME` and `HOME` to `tmp_path`.

- `is_auto_sync_exempt` (pure, argv+env only): every row of §2's table → `True` —
  `hooks`/`run`, each of the four exempt nouns, `install` and `uninstall` verbs across several
  nouns, the env opt-out — and a representative carrier (`pdata`/`list`) → `False`.
- `decide_auto_sync` (pure, no I/O): synced → `SKIP_SYNCED`; stale → `APPLY`; never-synced
  (`None`) → `APPLY`; failed attempt for this version inside the window → `SKIP_BACKOFF`; the same
  attempt outside the window → `APPLY`; a failed attempt recorded for a *different* version →
  `APPLY`.
- `ensure_synced` does not open `sessions.db` at all for an exempt caller (assert with a spy on
  `get_synced_version`) — the property §2's hot-path exemption depends on.
- `ensure_synced` end-to-end against a `tmp_path` HOME: applies and advances the marker; a
  failing step records all three failure keys and does **not** advance the marker; the four
  healthy steps still applied after one step failed; a successful apply clears stale failure keys.
- Exit-code independence: the requested command's rc is returned unchanged when auto-apply
  succeeds, when it fails, and when it is skipped for lock contention.
- Stream discipline: nothing an auto-apply prints appears on stdout, in the success, failure and
  backoff paths. Assert against captured stdout, not just stderr.
- Lock: a second `ensure_synced` with a live-pid lock file present skips the apply and returns
  promptly; a lock file naming a dead pid is reclaimed and the apply proceeds.
- `run_install_everything(health_check=False)` does not call `_cmd_doctor`; `health_check=True`
  (the `_cmd_install_everything` adapter path) still does — guards against auto-apply silently
  acquiring the ~1.5 s PyPI dependency.
- `check_install_everything_synced`: OK / WARN / new FAIL branch.
- Existing `tests/test_ccst_main_gate.py` is rewritten (the block-and-exit assertions no longer
  describe the product); `tests/test_install_sync.py` and `tests/test_ccst_install_everything.py`
  extended rather than replaced.
- `proc_lock` extraction (§3): the existing `tests/scheduler` lock tests must pass unchanged.
