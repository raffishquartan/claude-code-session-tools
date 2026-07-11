---
name: reduce-persistent-context
description: Measure the fixed per-session context footprint (CLAUDE.md, skill descriptions, MCP tool names, hooks, harness baseline), rank reduction candidates by token-saved-per-risk, and apply approved reductions behind 8-digit confirmation. Use when the user asks to "reduce context", "what's eating my context window", "shrink persistent context", "audit my context footprint", "trim startup overhead", or "/reduce-persistent-context".
---

# reduce-persistent-context

Measures the **persistent context** — everything loaded into every session
before the user types anything — attributes token cost per contributor, ranks
reduction candidates by token-saved-per-risk, and applies approved reductions
behind per-item 8-digit confirmation.

Persistent context = fixed per-session overhead, NOT per-task usage.

Sibling skill: `review-session-and-improve-claude-setup` analyses one session's
*dynamic friction*. THIS skill owns the *static footprint* ("is it worth its
token weight"). Keep them distinct.

## Prerequisites

- `claude-code-usage` on PATH (the analyse-cc-usage repo).
- Run all `python3` commands from the skill's `scripts/` directory so sibling imports resolve.

## Step 1 — Capture the live persistent blocks

Most persistent context is visible to you right now (it is in your prompt).
Write the four blocks below into `<session>/working/captured-context.txt`, each
under its EXACT marker line. The analyzer errors if any marker is missing.

```
### DEFERRED_TOOL_NAMES
<the full deferred-tool names list from the system reminder>

### SESSIONSTART_HOOKS
<the SessionStart hook reminder output>

### MCP_INSTRUCTIONS
<the per-MCP-server instructions text>
```

CLAUDE.md files and SKILL.md frontmatter are read from disk by the analyzer —
do not paste them. Skill descriptions are also measured per-skill from disk, so
do NOT capture the rendered skill-descriptions block (it would double-count).
Only the system-prompt/harness baseline is estimated (the one thing you
genuinely cannot see); it is flagged `(estimated)` in the report.

## Step 2 — Run the analyzer

```bash
cd ~/.claude/skills/reduce-persistent-context/scripts
python3 analyze_context.py \
  --captured <session>/working/captured-context.txt \
  --out <session>/out \
  --project-root <path to the project root being audited>
```

`--project-root` is the cwd of the session being audited (NOT the skill
directory you just `cd`ed into) — it is where that project's own CLAUDE.md
lives, if it has one. Omit it only if the audited session has no
project-level CLAUDE.md; the report then simply carries no project-CLAUDE.md
row (same as the global-CLAUDE.md-absent case).

Writes `context-report.json` and `context-report.md`. Usage is joined from
`claude-code-usage` over the last 90 days (`--since` to override) across the
mcp, plugin, and tool dimensions.

## Step 3 — Present the checkpoint table

Show the ranked table from `context-report.md`, adding two columns:

- **Risk** — blast radius of the change.
- **Recoverable?** — is the target restorable? A skill dir that is a symlink
  into a repo (e.g. `claude-code-session-tools` or `claude-code-config-sync`)
  is restorable; an installed package or a plain dir is not. Check before
  proposing `rm`.

Tiers: `strong` (high-cost + unused, or a redundant duplicate) → propose
removal/dedup; `trim` (high-cost + used) → propose shortening, not removal;
`mention` (low-cost + unused) → note only; `keep` → leave alone.

## Step 4 — Gated apply (ascending risk)

Apply ONLY what the user approves, one item at a time. Each destructive or
config change requires a fresh 8-digit code via the `generate-8digit-code`
skill, typed back by the user, before you act.

1. **Delete redundant duplicate skill dir** — `rm -rf` the non-canonical member
   of a pair (confirm recoverability first).
2. **Shorten a bloated skill description** — edit the `SKILL.md` frontmatter.
3. **Disable an MCP server / plugin** — set its entry to `false` in
   `~/.claude/settings.json` `enabledPlugins`.
4. **Trim/restructure CLAUDE.md** — move rarely-needed detail into an on-demand
   skill. Riskiest: show the diff and get approval before applying.

## Step 5 — After apply

- This skill is symlinked from `~/.claude/skills/reduce-persistent-context` into
  `claude-code-session-tools` (`skills/reduce-persistent-context/`). If the apply
  step edited a file under that symlink target, remind the user the change lives
  in the CCST repo checkout and needs its own commit/PR there.
- Do NOT auto-commit or push. Respect the 8-digit-PII rule on anything written.

## Tests

From the CCST repo root: `uv run pytest skills/reduce-persistent-context/tests -v`
(or, working purely inside the deployed skill directory:
`cd ~/.claude/skills/reduce-persistent-context && python3 -m pytest tests/ -v` —
this still works standalone because pytest's own rootdir-relative conftest
discovery finds `tests/conftest.py` regardless of cwd).
