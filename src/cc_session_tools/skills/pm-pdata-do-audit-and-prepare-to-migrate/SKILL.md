---
name: pm-pdata-do-audit-and-prepare-to-migrate
description: Full consistency/completeness audit of a project's central and recordkeeping files - OneDrive sync-conflict cleanup, cross-checking every index/register/log against what it claims to describe, and a pdata-migration readiness pass. Ends by writing a readiness manifest file that feeds `ccst pdata init` (via `pm-pdata-do-init`) - the tool-native migration mechanism - and, only for the narrow record shapes it can't express, the deprecated `pm-pdata-do-manual-migration` fallback. Triggers on "review all my central files", "audit the project files", "check everything is up to date and consistent", "get this project ready for pdata", "/pm-pdata-do-audit-and-prepare-to-migrate", or any request for a thorough review/health-check of a project's index/log/register files (as opposed to a single-session wrap-up, which is pm-update-central-files). Especially relevant when a project is scheduled to migrate to pdata - this audit is the recommended prerequisite pass, since a schema design should not be built against files that still have unresolved sync-conflict duplicates or known dangling rows.
---

# Audit central files

> **This audit feeds `ccst pdata init`, not a manual migration.** The readiness manifest (Phase 5)
> is a pre-flight pass for `ccst pdata init` (driven by `pm-pdata-do-init`), the default migration
> mechanism. `pm-pdata-do-manual-migration` is a deprecated fallback for record shapes `ccst pdata
> init` cannot express, not the default next step.

## When to use this vs. `pm-update-central-files`

`pm-update-central-files` is a single-session wrap-up (WORKLOG, memory, registering new `out/`
files). This skill is a project-wide sweep of every central file (indexes, logs, registers, CSVs),
run on demand, that can span a session or more. Use it when the whole project's recordkeeping needs
a health check, not just today's session tidied up.

## Overview

Five phases, each leaving a durable artefact. Four things called "readiness" are distinct:
the scan (`ccst pdata readiness-scan`, computed on demand), `pdata-readiness-notes.md` (Phase 4's
findings list), the Phase 5 manifest (`analysis/pdata-readiness.md` or equivalent), and
`.pdata-migration-manifest.json` (`ccst pdata init`'s own classification, not written here).

1. **OneDrive sync-conflict cleanup** (synced folders only).
2. **Readiness scan, then judgement audit** of each central-file domain.
3. **Consolidated report + checkpoint table.**
4. **pdata-readiness pass:** every blocker, file by file.
5. **Readiness manifest** for `ccst pdata init`.

Nothing destructive happens without the user's go-ahead. Deletions are always handed over as a
reviewable `rm` script, per `resolve-onedrive-conflicts`; never run `rm` yourself, even after
approval.

## Phase 1: OneDrive sync-conflict cleanup

Invoke `resolve-onedrive-conflicts` and run its scanner across the project tree; run it before
anything else so stale device-suffixed copies do not pollute later phases. Treat a
disproportionate `true_conflict` count as a scanner false-positive to investigate (the hyphen
heuristic can match a project's own multi-hyphen naming) and filter to genuine device-suffix
conflicts before trusting it. Split them into **central/index files** (a handful, reviewable
directly) and **bulk content files** (dozens or hundreds; give them their own agent with the
file list).

## Phase 2: readiness scan, then judgement audit

**Step 1 - run the scan before any per-domain checking or agent dispatch:**

```
ccst pdata readiness-scan --project <name>            # whole project, compact Markdown
ccst pdata readiness-scan --project <name> --format json
ccst pdata readiness-scan --project <name> --path <domain-dir>/ --findings-only   # one domain's slice
```

It is read-only and deterministic. It reports, per CSV, `ragged-rows`, `bad-header`,
`mixed-date-formats`, `mixed-separators` (`|` with `;` only), `machine-paths`, `null-strings`,
`duplicate-rows`, `comment-rows`, `repeated-header`, `no-unique-column`, `bom`, `crlf` and
`unreadable`, plus row counts and unique columns. Treat findings as leads to verify, not facts to
act on (a lone `-` may be a real dash). Its scanned set equals what `ccst pdata init` will
classify. Row numbers are data records, not line numbers.

A **unique column is not a stable key.** Whether it is one is a judgement, and the scan is input
to that judgement, not its answer: watch especially for `row_id`-style columns numbered
independently by different sessions or machines - these are NOT stable keys even though they look
like them. A `bom` is what turns a header garbled
under `ccst pdata init`; `crlf` is worth normalising first.

**Step 2 - judgement audit.** Group the central files the project's own instructions document,
plus anything on disk that is not documented (itself a gap), into domains (usually one per
top-level folder with its own index). With **three or fewer domains**, audit them yourself in one
pass. With **more than three**, dispatch one Sonnet-tier agent per domain in parallel, per
`dispatching-parallel-agents` and `select-agent-model`, giving each only its own domain's scan
slice (`--path`, `--findings-only`). Agents check, with concrete evidence for every claim:

- **File-to-index correspondence:** every file an index describes exists; every file that should be
  indexed has a row.
- **Controlled vocabularies:** any documented enum column uses only documented values.
- **Cross-reference integrity:** every row_id or back-reference resolves.
- **Documented but missing:** every file or folder the project's instructions describe exists. This
  rot is common and durable because a merely-referenced file that is absent breaks nothing loudly.
- **Derived-artefact completeness:** each source has its documented mirror, and no mirror is
  silently empty.
- **Own-documentation accuracy:** every path, count and reference in an atlas or index-of-indexes
  checked against the live filesystem.
- **The scan's blind spots:** multi-schema single files (comment banners, section headers, a second
  table under one file - `repeated-header` and `comment-rows` are only hints) and
  comma-versus-semicolon list columns.
- **Key stability:** whether each unique column is really a stable key.

Agents work read-only unless a fix is unambiguous and mechanical (merging two non-overlapping logs);
anything needing editorial judgement is a recommendation. Use `resolve-onedrive-conflicts`'s
`DELETE-SAFE` / `MERGE-NEEDED` / `RENAME` / `NO ACTION` verdicts for consistency.

## Phase 3: consolidated report + checkpoint table

Write one consolidated report to the session's `out/` covering every domain and the scan, and give
the user a checkpoint table split into:

- **Unambiguous fixes** ready on a single approval (documentation corrections, verified merges).
- **Genuine judgement calls** to weigh individually (ambiguous ownership, prune-vs-keep, which of
  two diverged forks is authoritative).
- **Real gaps** found but not fixed (missing extractions, undersized indexes, structural drift):
  recommend a project TODO entry or a prompt file for a dedicated session.

Hand back one reviewable delete script for every confirmed-safe deletion, exactly per
`resolve-onedrive-conflicts` (comment header with each item's evidence, `set -e`, one `rm --` per
line, told to the user as "review, then run yourself").

## Phase 4: pdata-readiness pass

Compile `pdata-readiness-notes.md` (or equivalent): fold in the scan's findings and add what it
cannot see, listing file by file every blocker a `pm-pdata-design-schema` session would hit:

- Inconsistent separators within one logical column (including comma/semicolon).
- No safe, durable, cross-session-stable natural key - especially independently-numbered `row_id`
  columns.
- Mixed date/timestamp formats.
- A file that is really several differently-shaped tables: needs a "split into multiple record
  collections" decision, not a naive single-table import.
- Machine-specific or session-relative paths in a column.
- Null conventions that are not real nulls (`"NO DATA"`, an empty cell that means something else).
- A `bom` or `crlf` file that will need normalising.

This file is a findings list, not a migration plan.

## Phase 5: write the readiness manifest (for `ccst pdata init` / `pm-pdata-do-init`)

Once the audit settles (typically two consecutive clean verification rounds, or a documented
pragmatic stop), write one manifest at a stable location (`analysis/pdata-readiness.md` for
projects with an `analysis/`-style folder, else the project's own top-level docs location), and
record its location in the project's instructions file. Sections, in this order, so `pm-pdata-do-init`
(or the deprecated `pm-pdata-do-manual-migration` fallback) can parse it deterministically:

1. **Header:** audit date, session/agent identifier, and an explicit "STALE IF" note (e.g. "stale
   if any file below was edited after `<date>`").
2. **File inventory:** every central/index/recordkeeping file covered, with full path, row/entry
   count, last-verified-clean date, and role (`index`, `register`, `log`, `narrative`,
   `derived-artefact`).
3. **Per-file migration blockers:** Phase 4's content, in the shape `pm-pdata-design-schema` and
   `pm-pdata-do-init` consume.
4. **Recommended natural key per file** where one exists cleanly, else an explicit "no stable key
   found, needs a synthetic id".
5. **Known-acceptable duplication:** anything duplicate by design, so a migration does not re-flag
   it or silently drop one copy.
6. **Open decisions:** anything needing the owner's judgement and not resolved; a later session must
   not treat an unresolved item as settled.

## Common mistakes

- Trusting the conflict scanner's raw `true_conflict` count without checking for the naming-pattern
  false positive.
- Treating the scan's unique columns as recommended keys, or its findings as facts to fix blindly.
- Assuming documentation implies existence: check every documented path.
- Running `rm` yourself after approval instead of handing back a reviewable script.
- Dispatching one do-everything agent, or a fleet for a three-domain project: use one agent per
  domain only above three domains.
- Skipping the readiness pass because "the project isn't migrating yet": if a `.pdata-db-dump/` or a
  pdata mention exists, it earns its cost early.
- Silently fixing a judgement call an agent flagged: "safe to apply" and "needs a human decision" are
  different buckets even when both are technically fixable.
