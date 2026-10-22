# Shipped pdata-migration follow-up prompts — design spec

Status: DRAFT, awaiting review. Source: two inter-session messages from
`claude-resume-home-migration` (2026-08-20), sent alongside a third ("How should ccst help set up
`~/cc/<project>` consistently?") that Chris has explicitly asked to hold and discuss directly,
not via ccmsg, once this set of requirements is spec'd out. This spec covers only the two
narrower, already-actionable asks; it does not prejudge that broader question.

## Problem

`ccst pdata init --write` migrates a project's flat files into its SQLite store, but two kinds of
follow-on work are explicitly out of scope for `pdata init` itself and currently have nothing
prompting anyone to do them:

1. The project's own `CLAUDE.md` (and possibly other doc files) may still describe the
   pre-migration flat-file layout.
2. Any of the project's Claude Code **skills** that read/write the old flat-file paths directly
   keep working against paths that no longer exist post-cutover.

Confirmed real on `home`: none of its three Tesco skills (`download-tesco-orders`,
`do-tesco-shop`, `check-tesco-shop-due`) have been updated yet, and all three still contain literal
instructions to read/write `~/cc/home/data/tesco/*.csv`/`*.json`, which the migration removed.

## Precedent — how ccst already ships a standalone prompt

`docs/global-claude-md-bootstrap-prompt.md` is exactly this pattern already: a self-contained
markdown prompt, run via `claude -p "... use this file as your prompt: docs/..."`, referenced from
`README.md`. That prompt is invoked manually against a checked-out source tree, though — it is
**not** resolved through the installed-package path, so it isn't available to `ccst pdata init`
running from an arbitrary project directory against an installed wheel.

For prompts a CLI command needs to point at from anywhere, the correct precedent is instead how
**skills** ship: `src/cc_session_tools/skills/**/*` is declared as package-data
(`pyproject.toml`'s `[tool.setuptools.package-data]`), and resolved at runtime via
`_discover_source_dir()` in `cli/ccst.py:92-107`:

```python
candidate = Path(__file__).resolve().parent.parent / "skills"
```

— a path that sits at the same relative depth whether `cc_session_tools` is an editable source
checkout or an installed wheel, with no install-location fallback (a fallback would just mask a
broken install).

## Design

### New package-data directory: `src/cc_session_tools/prompts/`

Two new self-contained prompt files, each written in the same style as
`docs/global-claude-md-bootstrap-prompt.md` (step-by-step instructions for an agent to follow,
including a repo/project-context sanity check at the top):

- `src/cc_session_tools/prompts/pdata-migration-claude-md-update.md`
- `src/cc_session_tools/prompts/pdata-migration-skills-update.md`

Add `"prompts/**/*"` to `pyproject.toml`'s existing `package-data`/`exclude` entries (mirroring
the existing `skills`/`config` entries exactly — same `exclude` reasoning applies, since
`prompts/` is bundled data, not an importable sub-package).

A new `_discover_prompts_dir()` in `cli/ccst.py`, identical in shape to `_discover_source_dir()`:

```python
def _discover_prompts_dir() -> Path:
    candidate = Path(__file__).resolve().parent.parent / "prompts"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(
        "Cannot locate bundled prompts/ directory. Run from the source tree or "
        "reinstall ccst."
    )
```

### Prompt content — informed by a first-pass survey, not fully speced here

The two prompts' actual step-by-step content depends on the range of doc/skill conventions across
real `~/cc/<project>` directories, per the source message's explicit ask to "review every project
under `~/cc/` first ... so the shipped prompt is informed by the real range of patterns from the
start". A first-pass survey (top-level listing of every `~/cc/*` directory, 2026-08-20) found:

- Every project has a `CLAUDE.md` and a `cc-sessions/` directory; beyond that, structure varies a
  lot — `deauppet`/`maxella`/`pbt` (legal/tracking projects) keep many top-level `.csv`/`.md`
  tracking files plus subject-matter folders (`correspondence/`, `evidence/`, `timeline*`);
  `home`/`pod` are pdata-migrated or pdata-shaped (`data/`, `correspondence/`); `future` has a
  deep `analyses/`/`business/`/`children/`/... topic-folder tree with an `INDEX.md`; `oneshot`
  keeps `files/`; `biz`/`fire` are near-empty (`CLAUDE.md` only, or just `cc-sessions/`).
- No project currently has a doc file that enumerates "which skills reference my data paths" —
  this genuinely doesn't exist anywhere yet, confirming the skills-update prompt is new ground,
  not a rediscovery of an existing convention.

This survey is a starting point, not the final input — Chris wants to walk through the design of
"what ccst standardises on across `~/cc/`" directly (the held third message) before either prompt's
content is finalised, since the two prompts and that broader question share the same survey data
and will likely want to agree on the same conventions. **Task 3 below is gated on that
conversation happening first**; Tasks 1-2 (the shipping mechanism and the CLI reminder wiring) do
not depend on it and can proceed immediately.

### CLI wiring

**Dry-run** (`ccst pdata init --project <name>`, no `--write`) — after the existing
`print(f"\nProposal: {result.proposal_path}")` in `_cmd_pdata_init`, add:

```python
print(f"After a successful --write, see: {_discover_prompts_dir() / 'pdata-migration-claude-md-update.md'}")
```

**`--write` success** — after the new `Verify: ...` line from
[[2026-08-20-pdata-init-write-usability-design]], add both reminders (the two source messages
together ask for the doc-update reminder to appear at both dry-run and write-success time, and
the skills-update reminder specifically at write-success time, since skills only actually break
once cutover has happened):

```python
prompts_dir = _discover_prompts_dir()
print(f"\nUpdate project docs: {prompts_dir / 'pdata-migration-claude-md-update.md'}")
print(f"Update consuming skills: {prompts_dir / 'pdata-migration-skills-update.md'}")
```

## Out of scope

- The two prompts' full step-by-step content (Task 3, gated per above).
- Any change to `ccst pdata init`'s own classification/dry-run/write behaviour.
- The broader "how should ccst help set up `~/cc/<project>` consistently" question — held for
  direct discussion with Chris, not built here or replied to via ccmsg.
