## Why

`ccst claude-md install` only ever manages one fixed block (`messaging`). A full source-code
review for the README rewrite (this cycle) surfaced two other real gaps this same mechanism
should close: (1) the `working/`→`out/` session-file convention and the agent-working-folder
convention are load-bearing for other CCST behavior (the `worklog-guard` hook, `ccs --contents`,
`pdata reconcile-session-output`, and the shipped `do-executor-critic-assessor-loop` skill, which
already points at a personal, unshipped `agent-usage.md` for the agent-folder contract) but are
never taught to a fresh installer's Claude; (2) the `confirm-8digit` hook and
`generate-8digit-code` skill exist with no always-on explanation of how they work together. A
single hard-coded block can't grow to cover these without becoming an unrelated grab-bag that's
impossible to install/uninstall piecemeal.

## What Changes

- Generalize `claude_md_install.py` from one hard-coded sentinel block to a registry of
  independently-installable named sections (`SECTION_IDS`), each with its own
  `<!-- CCST:<id> START/END -->` sentinel pair. The existing `messaging` section's rendered
  output is unchanged, byte-for-byte, so already-installed users see no diff on next install.
- Add three new sections: `workflow` (session working-file conventions), `agents` (subagent
  working-folder conventions), `confirm-gate` (how the 8-digit confirmation gate works).
- `ccst claude-md install`/`uninstall` gain a repeatable `--section {messaging,workflow,agents,
  confirm-gate}` argument; omitting it processes all four (in registry order). Both commands now
  print one result line per section instead of one line total.
- `install_claude_md`/`uninstall_claude_md` now take `sections: Sequence[str] | None = None` and
  return `list[MarkdownResult]` (one per processed section) instead of a single result -
  **BREAKING** for any direct caller of the library function (not the CLI, which is
  backward-compatible: omitting `--section` still installs/uninstalls everything the old
  single-block version did, plus the three new sections).
- Fix `do-executor-critic-assessor-loop`'s `SKILL.md`, which pointed at `agent-usage.md` (a
  personal, unshipped file) for the agent-folder contract - it now points at the shipped `agents`
  CLAUDE.md section.
- Fix `session_tag` hook's `SessionStart` message, which told the model to follow an undefined
  "CLAUDE.md startup flow" and produce a "hooks report" - neither exists in this package; the
  message now states only what is actually true.

## Capabilities

### New Capabilities

(none - this extends an existing capability rather than introducing a new one)

### Modified Capabilities

- `install/claude-md-fragment`: the fragment is no longer one fixed block - it is a registry of
  independently-installable sections, each with its own content contract. Adds three new
  sections' content requirements (`workflow`, `agents`, `confirm-gate`) alongside the existing
  `messaging` section's (unchanged) requirements.

## Impact

- `src/cc_session_tools/lib/claude_md_install.py` - registry/section-id model, per-section
  sentinel validation, single atomic write per call.
- `src/cc_session_tools/cli/ccst.py` - `--section` argument on `claude-md install`/`uninstall`,
  updated handlers and help text/docstring.
- `tests/test_claude_md_install.py` - extended for multi-section behavior, including a
  byte-for-byte pin test for the unchanged `messaging` section.
- `src/cc_session_tools/skills/do-executor-critic-assessor-loop/SKILL.md` - stale reference fixed.
- `src/hooks/session_tag.py` - `SessionStart` message wording fixed (no spec-level behavior
  change; the hook's inputs/outputs/event wiring are unchanged, only message text).
- No database or on-disk data format changes; no migration required. Existing installs of the
  `messaging` section are unaffected until the user opts into the new sections.
