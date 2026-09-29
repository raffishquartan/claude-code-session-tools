## Context

See `proposal.md` - Why. `claude_md_install.py` previously hard-coded one sentinel pair and one
block of Markdown; `install_claude_md`/`uninstall_claude_md` each returned a single
`MarkdownResult`. The CLI (`ccst claude-md install`/`uninstall`) had no way to select a subset.

## Goals / Non-Goals

**Goals:**
- Add three new sections without disturbing the existing `messaging` section's installed state
  for anyone who already has it.
- Make sections independently selectable, matching the existing `ccst hooks install --hook NAME`
  pattern other CCST install commands already use.
- Keep the file write atomic and single-pass even when several sections are processed together.

**Non-Goals:**
- Partial-write/partial-failure semantics across a mixed batch (e.g. "install workflow even if
  agents' markers are malformed, but only if agents wasn't requested") beyond what's needed for
  the "malformed section doesn't block an unrelated one" scenario in the spec. A default
  `sections=None` call still validates every section's sentinels up front and raises before any
  write if any is malformed - same all-or-nothing semantics as the original single-block
  behavior, just correctly scoped to only the sections actually being processed.
- Automatically re-applying the new sections to already-installed users' live `~/.claude/CLAUDE.md`
  as part of this change - that happens the normal way, via `ensure_synced()`'s existing
  version-mismatch auto-`install-everything --apply` the next time they run `ccst` after
  upgrading, or by hand via `ccst claude-md install`.

## Decisions

- **Per-section sentinel pairs (`<!-- CCST:<id> START/END -->`) over one shared sentinel with
  sub-headers.** A shared sentinel would make "install just `workflow`" impossible to express in
  the file itself - there'd be no way to know which `##` sub-sections came from which install
  call. Per-section sentinels make each section's presence independently detectable and
  removable, at the cost of slightly more sentinel-line noise in the file.
- **`messaging`'s sentinel and body text left byte-for-byte identical**, verified by a pinned
  test. Alternative considered: rename the sentinel to something more general (e.g.
  `CCST:core`) now that it's one of several sections - rejected, because it would require a
  migration path for the sentinel string itself (find-old-sentinel, rewrite-to-new) for zero
  benefit; keeping the id `messaging` and simply adding siblings is strictly additive.
- **Registry-driven design (`SECTION_IDS` tuple + `_SECTION_BODIES` dict) over four separate
  hard-coded blocks.** Mirrors the existing `HOOK_VERBS`/`hook_registry.py` pattern elsewhere in
  this codebase, and lets the CLI build its `--section` choices from the same source of truth
  (`SECTION_IDS`) rather than a second hard-coded list that could drift.
- **One atomic write per `install_claude_md`/`uninstall_claude_md` call, covering every section
  processed, rather than one write per section.** Fewer disk writes, no window where the file is
  left with only some of a multi-section install applied if the process were interrupted
  mid-call, and it's the only design consistent with `--apply`'s existing all-dry-run-or-all-write
  contract.
- **`ValueError` (not a bespoke exception) for an unknown section id, raised before any file
  read/write.** `MalformedBlockError` already subclasses `ValueError`, so the CLI's existing
  `except ValueError` handling in both command handlers catches both cases with one branch,
  without needing to distinguish "your `--section` typo" from "your file's markers are broken" at
  the exit-code level (both are user-fixable input problems, exit 1, message to stderr).
- **`MarkdownResult` gains a `section` field; both library functions now return
  `list[MarkdownResult]`.** This is a breaking change to the library API (not the CLI - see
  proposal.md). Accepted because `claude_md_install` has no other in-repo callers besides
  `ccst.py`'s own two handlers (checked directly), and the return type change is required for the
  CLI to report per-section results, which is the whole point of the feature.

## Risks / Trade-offs

- [Risk] A future contributor adds a fifth section and forgets to update `SECTION_IDS` or forgets
  the CLI picks up new sections automatically since it derives `--section` choices from
  `SECTION_IDS` → Mitigation: `_resolve_sections` raises immediately on any id not in
  `SECTION_IDS`, and the CLI's `choices=CLAUDE_MD_SECTION_IDS` means an unregistered id is
  rejected by argparse before it ever reaches the library function - there's no path to silently
  ship a section the CLI can't select.
- [Risk] `agents`/`confirm-gate`/`workflow` prose could drift from what the corresponding hook or
  skill actually does over time (as happened with the two dangling-reference bugs found during
  this same review) → Mitigation: none automated yet; this is a known limitation of documentation-
  as-code without a doc/behavior consistency check. Out of scope for this change.

## Migration Plan

No data migration. Deployment is the existing `ccst install-everything --apply` / automatic
`ensure_synced()` path, unchanged. Rollback is a normal revert - the `messaging` section's content
never changes, so a rollback that removes the three new sections' code leaves any
already-installed `messaging` section exactly as it was.
