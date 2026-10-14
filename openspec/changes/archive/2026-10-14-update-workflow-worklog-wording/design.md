## Context

The `workflow` section body is a literal string in `_SECTION_BODIES` in `claude_md_install.py`. `install_claude_md()` replaces an existing block whenever it differs from the rendered template. The synced CLAUDE.md therefore always converges on the template, so the wording must live in the template. See proposal.md - Why.

## Goals / Non-Goals

**Goals:**
- Template WORKLOG paragraph matches the requested wording exactly, with unchanged line wrapping so the block is otherwise byte-identical.

**Non-Goals:**
- Drift detection or backup of hand-edited blocks (separate follow-up proposal).
- Any change to other sections or to `worklog-guard`.

## Decisions

- Edit the string in place rather than adding a new section: the paragraph already exists and only its text changes.
- Patch bump (3.15.1): no interface change, and the wording change reaches installs through the existing auto-sync.

## Risks / Trade-offs

- Installs with a hand-edited `workflow` block lose that edit on sync → this is existing behaviour, addressed by the follow-up drift-detection proposal.
