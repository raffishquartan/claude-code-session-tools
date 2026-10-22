# Shipped pdata-migration follow-up prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two reusable, package-bundled prompts (project-doc updates, consuming-skill
updates) alongside `ccst pdata init`, and have `ccst pdata init` remind the user where to find
them at the right moments, so post-migration follow-up work isn't silently dropped.

**Architecture:** New `src/cc_session_tools/prompts/` package-data directory, resolved at runtime
via a new `_discover_prompts_dir()` in `cli/ccst.py` (identical pattern to the existing
`_discover_source_dir()` for `skills/`). `_cmd_pdata_init` prints the doc-update prompt's path on
both dry-run and `--write` success, and the skills-update prompt's path on `--write` success.

**Tech Stack:** Python 3.13, pytest + subprocess-based CLI tests.

Spec: `docs/superpowers/specs/2026-08-20-pdata-migration-prompts-design.md`

**Gating note:** Task 3 (the two prompts' actual content) is blocked on a design conversation
with Chris about `~/cc/<project>` conventions (held per his answer on 2026-08-20 — do not draft
prompt content or send anything via ccmsg about this before that conversation happens). Tasks 1-2
do not depend on it and can proceed immediately; this plan is ordered so Tasks 1-2 land as a
complete, shippable unit even if Task 3 is deferred to a follow-up session.

Work happens in a dedicated worktree per this repo's `.claude/CLAUDE.md`:
```sh
git worktree add .worktrees/pdata-migration-prompts -b f/20260820-pdata-migration-prompts
cd .worktrees/pdata-migration-prompts
uv sync --extra dev
```

---

### Task 1: Bundle a `prompts/` package-data directory + discovery helper

**Files:**
- Create: `src/cc_session_tools/prompts/pdata-migration-claude-md-update.md` (placeholder content
  until Task 3 — see Step 3)
- Create: `src/cc_session_tools/prompts/pdata-migration-skills-update.md` (same)
- Modify: `pyproject.toml` (`[tool.setuptools.package-data]`, `exclude`)
- Modify: `src/cc_session_tools/cli/ccst.py` (new `_discover_prompts_dir()`)
- Test: new `tests/test_ccst_prompts_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
def test_discover_prompts_dir_finds_bundled_prompts():
    from cc_session_tools.cli.ccst import _discover_prompts_dir
    d = _discover_prompts_dir()
    assert d.is_dir()
    assert (d / "pdata-migration-claude-md-update.md").is_file()
    assert (d / "pdata-migration-skills-update.md").is_file()
```

- [ ] **Step 2: Run to verify red** — `uv run pytest tests/test_ccst_prompts_discovery.py -v`

- [ ] **Step 3: Implement**
  - Add `_discover_prompts_dir()` to `cli/ccst.py`, mirroring `_discover_source_dir()`
    (spec's exact snippet).
  - Create both prompt files with placeholder content: a title, a status note
    ("PLACEHOLDER — content pending the `~/cc/<project>` conventions discussion, see
    `docs/superpowers/specs/2026-08-20-pdata-migration-prompts-design.md`"), and nothing else.
    Real content lands in Task 3.
  - Add `cc_session_tools = ["skills/**/*", "config/*", "prompts/**/*"]` to
    `[tool.setuptools.package-data]`.
  - Add `"cc_session_tools.prompts", "cc_session_tools.prompts.*"` to the existing
    `[tool.setuptools.packages.find]` `exclude` list, same reasoning as the existing
    `skills`/`config` entries (bundled data, not an importable sub-package).

- [ ] **Step 4: Run to verify green**

- [ ] **Step 5: Verify the sdist/wheel actually includes the new files** — `uv build` then
  inspect the built wheel (`unzip -l dist/*.whl | grep prompts/`) to catch a package-data
  mis-declaration before it ships, matching how `skills/`'s inclusion would be verified.

---

### Task 2: Wire the CLI reminders

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` (`_cmd_pdata_init`)
- Test: `tests/test_ccst_pdata_init_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_dry_run_prints_doc_update_prompt_path(...):
    result = run_ccst(["pdata", "init", "--project", "demo"])
    assert "pdata-migration-claude-md-update.md" in result.stdout

def test_write_success_prints_both_prompt_paths(...):
    result = run_ccst(["pdata", "init", "--project", "demo", "--write"])
    assert "pdata-migration-claude-md-update.md" in result.stdout
    assert "pdata-migration-skills-update.md" in result.stdout
```

- [ ] **Step 2: Run to verify red**

- [ ] **Step 3: Implement** — add the two `print()` calls to the dry-run branch and the three
  (doc-update + skills-update; verify-command from the sibling usability plan may already be
  merged first, in which case just append after it) to the `--write` success branch, per spec.

- [ ] **Step 4: Run to verify green, plus the full pdata CLI test file**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -q`

---

### Task 3 (gated): Write the two prompts' real content

**Do not start this task until the `~/cc/<project>` conventions conversation with Chris has
happened.** When it has:

- [ ] Re-survey `~/cc/*` in full (not just top-level listing) for CLAUDE.md doc-reference
  patterns and skill data-path patterns, informed by whatever conventions came out of the
  conversation.
- [ ] Write `pdata-migration-claude-md-update.md`: steps to find every doc reference to the
  pre-migration flat-file layout in a project's `CLAUDE.md` (and other top-level docs) and update
  it to describe the pdata-backed store instead. Follow
  `docs/global-claude-md-bootstrap-prompt.md`'s structure (context-check step first, explicit
  idempotency note, numbered steps).
- [ ] Write `pdata-migration-skills-update.md`: steps to find every project skill (under
  `~/.claude/skills/`, matched to the project by name/content) that reads/writes the
  pre-migration flat-file paths directly, and update it to use `ccst pdata` commands instead.
- [ ] Replace both placeholder files from Task 1 with this real content.
- [ ] Test against `home`: run both prompts for real against `~/cc/home`. Expect the CLAUDE.md
  prompt to find little (Chris has already hand-updated `home/CLAUDE.md`) and the skills prompt to
  find real work (the three Tesco skills' literal old-path references).

---

### Task 4: Full verification + docs

- [ ] Run: `uv run pytest -q` — full suite green.
- [ ] Run this repo's configured lint/type checks (check `pyproject.toml` / CI workflow).
- [ ] CHANGELOG.md entry — minor bump (new feature: additive CLI output + new bundled prompts, no
  breaking change) per this repo's version policy.
- [ ] Update `README.md` if it documents `ccst pdata init`'s output, to mention the new reminder
  lines.

### Task 5: Ship

Follow superpowers:finishing-a-development-branch once all tasks are green. If Task 3 is deferred,
ship Tasks 1-2 as their own PR with placeholder prompt content rather than blocking the CLI wiring
on the design conversation.
