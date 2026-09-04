## Context

See proposal.md - Why. Background research:
`cc-sessions/20260904-update-ccst/working/2.14.0-research.md` (this repo) - read that file for
full file:line citations and verbatim quotes; this design references its findings without
repeating them.

## Goals / Non-Goals

**Goals:**
- A genuinely new project gets a visible, deletable starting folder structure from
  `ccst pdata init`, without disturbing any existing or differently-structured project.
- The manifest filename change is fully backward-compatible: no existing migrated project needs
  manual intervention.
- `ccst pdata verify` can tell "never migrated" apart from "migrated, manifest now missing" using
  evidence already on disk, without requiring a schema change or new data store.
- `pm-pdata-audit` and `pm-pdata-migrate` install the same way every other bundled skill does.

**Non-Goals:**
- Not building a command to reconstruct/regenerate a lost manifest from `.pdata-migrated/`
  content + `.db` row counts (research confirmed no such command exists today) - out of scope;
  the hardened verify check's job is to detect and point at manual reconciliation steps, not
  perform the reconciliation itself.
- Not scaffolding `analysis/` or `workstreams-archived/` - the research survey found no project
  using the latter (the skill itself calls it "not yet observed anywhere"), and `analysis/`
  appears with an inconsistent name (`analyses` in one project) precisely because it's
  work-product-shaped and project-specific in a way the other three aren't.
- Not touching `home`/`pod`'s existing `data/`-folder convention or any other project's existing
  layout - scaffolding only ever fires for a project whose root did not exist before the call.
- Not renaming the on-disk `pm-pdata-audit` skill directory to match the task list's informal
  "pm-audit-central-files" reference - the actual, already-built, already-portable skill is named
  `pm-pdata-audit`; the task list's title was imprecise, not a rename request.

## Decisions

### 1. Folder scaffolding: three named folders, new-project-only, deletable by design

Scaffold exactly `correspondence/`, `meetings-and-calls/`, `workstreams/` (the proposal's named
set, a proper subset of `pm-project-layout-reference`'s five) only when
`resolve_project_root()`'s target directory does not already exist at the start of the call -
capture `root.exists()` before the `mkdir`, scaffold the three subfolders only when that was
`False`. This must happen in `init_service.write()`, not `dry_run()`: the research confirmed both
currently call `resolve_project_root()`, and scaffolding from `dry_run()` would create real
directories as a side effect of a command whose entire contract is "don't write anything" -
`write()` already sits behind the `--write` confirmation gate, which is the correct place for a
side effect this real.

**Alternative considered:** scaffold all five (including `analysis/` and
`workstreams-archived/`). Rejected per Non-Goals - directly contradicts the skill's own
"not every project needs all five" framing and the research survey's evidence, and
`workstreams-archived/` is a convention the skill admits is unobserved anywhere.

**Alternative considered:** scaffold nothing, leave it entirely to the skill's manual guidance
(status quo). Rejected - this is what item #16 exists to fix; the skill's guidance is judgment-
based advice for choosing which folders apply to a mature project's needs, not a substitute for a
new project having anything to look at on day one.

### 2. Manifest rename: single resolver function, not per-call-site fallback logic

Add one function to `init_paths.py`, `resolve_proposal_path(project_root: Path) -> Path`, that
returns the new-name path if it exists, else the legacy-name path if that exists, else the
new-name path (for a fresh write). Every current `.exists()` call site identified in the research
(`init_service.py:373`, `manifest.py:123`, `rename_group.py:77,111`, `verify.py:116`) switches to
calling this resolver instead of building `project_root / PROPOSAL_FILENAME` directly.
`PROPOSAL_FILENAME` itself becomes the new name (`.pdata-migration-manifest.json`); a new
`LEGACY_PROPOSAL_FILENAME = ".ccst-pdata-proposal.json"` constant documents the retired name for
the resolver and for anyone grepping history.

**Alternative considered:** duplicate the "check new name, fall back to old name" logic at each
of the 4-5 call sites individually. Rejected per this repo's coding standard ("hoist shared logic
to one source of truth") - the research explicitly flagged this as the cleaner option.

Non-functional literal duplicates (`cli/ccst.py:2975`'s argparse help text,
`skills/pm-project-init/SKILL.md:39,65`'s prose, and docstring mentions in `verify.py`,
`rename_group.py`, `init_paths.py`, `classify.py`) are updated by hand to the new name - these
aren't fallback-relevant (a user reads help text and docs before any file exists to fall back
from), so they simply describe current behavior.

### 3. Verify hardening: check `.pdata-migrated/` presence independently of the manifest

**Revised during implementation:** the original plan (below) treated "any populated record_group"
as independent evidence of a migration, alongside `.pdata-migrated/` presence. Running the
existing test suite immediately falsified this: several tests set up projects using
`ccst pdata add`/`service.add_record` directly, with real rows, that have never run
`ccst pdata init` and were never expected to have a manifest - the row-count signal flagged every
one of them as "migrated, manifest missing", a false positive on the majority of this project's
own test fixtures. `.pdata-migrated/` is confirmed (by grep) to be written by exactly one code
path, `cutover.py`'s classify-and-migrate step - it is unambiguous evidence of that specific flow.
Row counts are not: they can't distinguish "classified via init, manifest later lost" from
"never classified, just used `pdata add` directly" without the manifest itself to cross-reference
against, which is precisely the file that's missing.

Final design: before `check_row_count_parity()`'s current `if not proposal_path.exists(): return
[]` early return, add one independent check that runs whenever the manifest is absent - does
`project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME` exist and contain any entries? If so, the
project shows unambiguous migration evidence with no manifest to explain it - append a new
`VerifyIssue` (`FAIL` - this is exactly as broken as "migration not yet run" was for the
common-store migration markers earlier this release, an operator has no way to know their project
needs attention) whose message names the two recovery commands the research confirmed already
exist (`ccst pdata schema show --project <project> --group <record_group>`, `ccst pdata schema
list --project <project>`) and describes manual reconciliation (compare `.pdata-migrated/<group>/`
file listings against `schema show`'s field list and `schema list`'s row counts) rather than
pointing at a fix command that doesn't exist. If the archive is absent too, keep returning `[]`
unchanged - genuinely nothing to compare, not a defect, exactly as today.

**Alternative considered:** treat manifest-absent-with-evidence as `WARN` instead of `FAIL`,
matching the common-store migration check's "already ran, files just weren't cleaned up" WARN
case. Rejected - that WARN case applies when the marker/manifest confirms success and only
cleanup is outstanding; here the manifest itself is the thing missing, so there is no confirmed-
successful marker to point to, only circumstantial evidence - closer to "can't confirm this
migrated cleanly" than "confirmed migrated, minor cleanup left," which is a FAIL-shaped gap in
this project's own convention (see the common-store migration check earlier this release: FAIL
means "can't confirm success", WARN means "confirmed success, cosmetic leftover").

### 4. Skill bundling: directory copy, no registry change

`git mv` (from the `~/.claude/skills/` originals, into this repo) `pm-pdata-audit/` and
`pm-pdata-migrate/` into `src/cc_session_tools/skills/`. No `pyproject.toml` change (the existing
`skills/**/*` package-data glob already covers new subdirectories) and no registry/manifest file
to update (`_discover_skills()` is a directory scan). Confirmed by research: both files are
already portable (zero personal/project-specific identifiers).

## Risks / Trade-offs

- [Risk] A project's root directory can be created by something other than `ccst pdata init`
  moments before init runs (e.g. `mkdir` in a setup script), making the "root did not exist"
  gate fire on what is, practically, still a same-session first init.
  → Mitigation: acceptable false-negative, not a false-positive - the gate only ever under-
  scaffolds (skips a genuinely-new project that happened to have its root pre-created), never
  over-scaffolds an existing project with real content. No data loss or clutter risk either way.
- [Risk] Renaming `PROPOSAL_FILENAME`'s value without care could break an in-flight dry-run/write
  pair if code elsewhere caches the old literal.
  → Mitigation: research confirmed exactly one non-constant-based literal (`ccst.py:2975`, help
  text only, not read back) - no runtime code path duplicates the string outside the constant.
- [Risk] The new verify check adds two filesystem/DB reads per project on every `ccst pdata
  verify` run, even for projects that never had a manifest at all (State 1, the common case).
  → Mitigation: both checks are cheap (one directory listing, one indexed-table row-count query
  already used elsewhere in this module) and only run in the branch that already short-circuited
  to a trivial `return []` - no meaningful performance change for the common case.

## Migration Plan

Purely additive/backward-compatible - no explicit migration step. The manifest rename is
transparent via the resolver (Decision 2); folder scaffolding only affects projects created after
this ships; the verify hardening only adds new issues, never removes existing detection; the two
newly-bundled skills only take effect on a machine's next `ccst skills install`/
`install-everything` run, matching how every other bundled-skill addition has always worked.

## Open Questions

None - Decisions 1-4 above resolve every ambiguity the research surfaced (the folder-name set,
the dry-run-vs-write scaffolding hook point, FAIL-vs-WARN severity for the new verify issue, and
the `pm-audit-central-files`/`pm-pdata-audit` naming mismatch).
