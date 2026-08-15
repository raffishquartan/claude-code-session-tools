# ccsched job scope (local vs cross-machine) and CCCS sync

Status: approved by Chris in session `20260812-fix-ccst`, 2026-08-12.

## Problem

`ccsched` jobs live in a per-machine SQLite store (`ccsched.db`) with no concept of scope —
every job is implicitly local to the machine it was registered on. Chris runs Claude Code on
two laptops kept in sync by `claude-code-config-sync` (CCCS), a git repo that is the source of
truth for `settings.json`, `CLAUDE.md`, skills, and hooks. Some jobs genuinely only make sense
on one machine (e.g. anything touching a machine-specific mount); others should run identically
on both. There is currently no way to declare a job cross-machine, and no mechanism for CCCS to
carry a job definition across the sync boundary.

CCCS's own `TODO.md` already documents the adjacent, unsolved problem of machine-specific
values inside files it syncs verbatim (`CCST_SCREENSHOT_DIR`, `permissions.additionalDirectories`
hold WSL paths meaningless on macOS). This design reuses the same root fix — install-time
`{{PLACEHOLDER}}` templating resolved from a machine-local values file — for ccsched job
commands, without attempting to solve the broader `settings.json` case.

## Non-goals

- Solving CCCS's general machine-specific-`settings.json`-values problem (noted above as
  future work the new machine-values file could extend to).
- Any form of automatic `git commit`/`git push` on Chris's behalf. Every git write stays a
  human/session-initiated action.
- Conflict resolution UI beyond what CCCS's existing drift model already provides (repo-wins
  for pull, warn-only for push).

## Repos touched

This spans two repositories with no shared build/test tooling: `claude-code-session-tools`
(§1, §3, §4, §6 — Python) and `claude-code-config-sync` (§2, §5 — bash). Plan as two
independent implementation plans, not one: a ccst-side plan and a CCCS-side plan, each with its
own tests and its own PR.

## Design

### 1. `scope` field on `JobSpec` / `BundledJob`

`src/cc_session_tools/lib/scheduler/jobspec.py`'s `JobSpec` and
`src/cc_session_tools/lib/scheduler/bundled_jobs.py`'s `BundledJob` both gain:

```python
class JobScope(str, Enum):
    LOCAL = "local"
    CROSS_MACHINE = "cross-machine"
```

`scope: JobScope = JobScope.LOCAL` — additive, default preserves every existing job's current
(implicitly local) behaviour with no migration required. `ccsched.db`'s `jobs` table gains a
`scope TEXT NOT NULL DEFAULT 'local'` column.

`ccsched add` / `ccsched edit` gain `--scope {local,cross-machine}` (default `local`, matching
the field default). `ccsched show` prints the configured scope alongside existing fields.

`BundledJob` gains the same field (always `LOCAL` for every current entry — bundled jobs ship in
the package itself and never go through the §6 cross-machine sync path regardless of scope value)
so `JobSpec` and `BundledJob` keep one shared shape; `validate_job_fields` builds a `JobSpec` from
either without a scope-shaped special case.

### 2. CCCS-authored manifest is the source of truth for cross-machine jobs

CCCS gains `config/ccsched-jobs.json`: a JSON array of job objects, same shape as `BundledJob`,
holding only `scope: "cross-machine"` jobs — CCCS has no reason to know about local-scope jobs.
Chris hand-edits this file (or a machine's `ccsched add --scope cross-machine` writes to it, see
§4) and commits it like any other CCCS-tracked config file.

### 3. Machine-specific values via `{{PLACEHOLDER}}` templating

A cross-machine job's `command` argv elements may contain `{{PLACEHOLDER}}` tokens (e.g.
`{{HOME}}/scripts/foo.sh`, matching the install-time-templating convention Chris's own CLAUDE.md
already establishes). Resolution happens at reconciliation time (§5) against a new machine-local,
**not git-tracked** values file — not CCCS's `config/settings.local.json`, which
`TODO.md` already flags as wrongly-scoped (project-scope-only, not machine-scope, per the Claude
Code docs). Path: `~/.claude/ccsched-machine-values.json`, a flat string→string map.

A placeholder present in a cross-machine job's command with no matching key in the local values
file is a hard validation error at reconciliation time, naming the missing key — never a silent
skip or invented default (per this repo's "no fallback values for inputs that should have been
validated upstream" standard).

### 4. `ccst` export command (local → CCCS direction)

A new `ccst ccsched-jobs export <job-id>` command serializes a local cross-machine job's current
`JobSpec` into the CCCS manifest's JSON shape (reversing §3's templating is out of scope for v1 —
export requires the operator to manually re-templatize any machine-specific values before
committing, since the tool cannot know which local value is meant to travel as a placeholder
versus stay literal). Printed to stdout for the operator to paste into
`claude-code-config-sync/config/ccsched-jobs.json` and commit — a normal `git commit`, not a new
push mechanism.

### 5. CCCS drift hook: review-gated pull, warn-only push

`check-config-drift.sh` (CCCS SessionStart hook) gains a ccsched-jobs check:

- **CCCS → local (pull), review-gated:** if a cross-machine job in the repo manifest differs
  from (or is absent from) the local `ccsched.db`, `check-config-drift.sh` reports it through
  CCCS's existing session-start digest — the same single surface `settings.json`/`CLAUDE.md`
  drift already uses — but does **not** apply it automatically, unlike that existing
  auto-apply-on-newer-repo behaviour. A scheduled job executes commands unattended; silently
  changing what it runs is a materially bigger risk than a text config diff. This check is
  entirely CCCS-side (bash); `ccst doctor` gains no new CCCS-awareness or repo-location logic —
  it has no role in detecting this drift. The operator runs `ccst ccsched-jobs sync --apply`
  (new command, §6) once they've looked at what changed. That command reads the manifest
  directly from a filesystem path — default `~/repos/claude-code-config-sync/config/ccsched-jobs.json`,
  matching the `REPO_DIR` location `check-config-drift.sh` itself already hardcodes, overridable
  with `--manifest PATH` for a non-default clone location — so it needs no CCCS-repo-discovery
  mechanism either, just a path.
- **local → CCCS (push), warn-only:** unchanged from CCCS's existing model — a local cross-machine
  job that differs from the repo manifest is reported as "not yet pushed"; the operator uses the
  §4 export command plus a manual commit.

### 6. Reconciliation policy split

Today's `_cmd_ccsched_jobs_install` (`ccst.py`) is deliberately install-only-if-missing: an
existing job id is never touched, because a human may have hand-edited it via `ccsched edit`.
That policy stays exactly as-is for local-scope and bundled jobs.

For cross-machine jobs it is wrong — CCCS being the declared source of truth must be allowed to
*update* an existing job, or the sync is pointless. `ccst ccsched-jobs sync --apply` (new
subcommand, separate from `ccsched-jobs install`) reads the CCCS manifest (with §3 templating
resolved), and for each cross-machine job: adds it if missing, or replaces it if the manifest's
definition differs from the local one. Local-scope jobs are untouched by this command; bundled
jobs are untouched by it (they keep going through `ccsched-jobs install`).

## Testing

- `JobSpec`/`BundledJob` scope field: default, explicit local, explicit cross-machine; DB
  migration adds the column with the correct default for pre-existing rows.
- `ccsched add`/`edit --scope`: valid values, invalid value rejected at the boundary.
- Placeholder resolution: all placeholders resolved, missing placeholder raises with the
  specific key named, no placeholders (plain command) unaffected.
- `ccst ccsched-jobs export`: known job id, unknown job id, a job with unresolved
  local-only values in its command (should still export — export does not attempt to reverse
  §3's templating).
- `ccst ccsched-jobs sync --apply` / dry-run: missing job added, changed job replaced, unchanged
  job left alone, local-scope jobs never touched, bundled jobs never touched; default manifest
  path resolves to `~/repos/claude-code-config-sync/config/ccsched-jobs.json`, `--manifest PATH`
  overrides it, a missing manifest file at the resolved path is a clear error (not a silent
  no-op).
- CCCS `check-config-drift.sh`: incoming cross-machine change reported (via the existing
  session-start digest) but not applied; local-ahead change reported as unpushed; no drift is
  silent. This is the only surface for cross-machine job drift — no `ccst doctor` test needed,
  since `ccst doctor` has no role here.
