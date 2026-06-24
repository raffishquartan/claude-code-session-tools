# CLAUDE.md Bootstrap Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `docs/global-claude-md-bootstrap-prompt.md` with three focused interactive-session prompts that cover (1) required CCST configuration, (2) recommended practices, and (3) a final contradiction check; wire all three into `install-everything.sh`; and update README.md to document the all-or-nothing nature of the hook/skill packages.

**Architecture:** Three prompt files in `docs/` each define the initial user message for a `claude` interactive session. `install-everything.sh` gains three new steps that print clear guidance and optionally launch each session. No new Python code; no new `ccst` subcommands (CLAUDE.md configuration intentionally stays outside `ccst` to prevent partial/inconsistent setup). Tests are structural: verify prompt files exist, contain expected sentinel markers, and are referenced by the install script.

**Tech Stack:** bash (install-everything.sh), Markdown (prompt files, README), pytest (structural tests)

---

## Background and design decisions

### The three prompt files

| File | Sentinel | Mode | Purpose |
|---|---|---|---|
| `docs/claude-md-required.md` | `ccst-required` | Interactive | 8-digit gate + session workspace convention. Both are required together (confirm-8digit hook + generate-8digit-code skill are a pair; cc-sessions dirs + WORKLOG + agent naming are a pack). |
| `docs/claude-md-recommendations.md` | `ccst-recommendations` | Interactive | Generalised agent practices: proactive agents, foreground/background, model selection, 5% context break-even, orchestrator-executor-critic-assessor, session conduct, task-list sharing. |
| `docs/claude-md-check.md` | — | `-p` (print/oneshot) | Read full CLAUDE.md, report duplications/contradictions/stale references. Detection only. |

### All-or-nothing package groupings (for README)

Three packages, each cohesive:

- **Mechanical safety hooks**: `session-tag` + `prompt-guard` + `bash-security-review`. Work standalone; no CLAUDE.md dependency.
- **Confirmation gate**: `confirm-8digit` hook + `generate-8digit-code` skill + `<!-- ccst-required -->` section (8-digit part). These are useless individually. The hook blocks tool calls; the skill generates valid codes; CLAUDE.md tells Claude which actions are gated and not to invent numbers.
- **Session workspace**: `edit-write-audit` hook + `session-end` hook + `find-claude-code-session` / `move-session` / `list-empty-sessions` / `delete-sessions` / `analyse-cc-usage` skills + `<!-- ccst-required -->` section (session workspace part). The hooks fire on WORKLOG.md paths; the skills navigate cc-sessions; CLAUDE.md defines the convention agents write to.

### What the old bootstrap prompt covered

`docs/global-claude-md-bootstrap-prompt.md` covered: CCST install detection, session management CLI guidance, skills list, 8-digit gated actions (interactive), and idempotent write. Its content is redistributed as follows:
- Install detection → stays as preamble in `claude-md-required.md`
- Session management CLI guidance → moves to `claude-md-recommendations.md`
- Skills list → already self-described by skill metadata; omitted from prompts
- 8-digit gated actions → `claude-md-required.md`
- Idempotent write → both `claude-md-required.md` and `claude-md-recommendations.md`

---

## File map

**Create:**
- `docs/claude-md-required.md` — interactive prompt: 8-digit gate + session workspace
- `docs/claude-md-recommendations.md` — interactive prompt: agent practices + session conduct
- `docs/claude-md-check.md` — oneshot prompt: contradiction/duplication check
- `tests/test_claude_md_prompts.py` — structural tests for prompt files + install script references

**Modify:**
- `install-everything.sh` — add steps 6, 7, 8 (required config, recommendations, check)
- `README.md` — update install section (4-step process), add package-groupings section, update Configure section

**Delete:**
- `docs/global-claude-md-bootstrap-prompt.md` — superseded by the three new prompts

---

## Task 1: Feature branch

- [ ] **Step 1.1: Create worktree and branch**

```bash
git worktree add .worktrees/claude-md-bootstrap -b f/20260517-claude-md-bootstrap-redesign
cd .worktrees/claude-md-bootstrap
uv sync --extra dev
```

- [ ] **Step 1.2: Verify tests pass on the branch before any changes**

```bash
uv run pytest -q
```

Expected: all tests pass (clean baseline).

- [ ] **Step 1.3: Commit (empty) to mark baseline**

```bash
git commit --allow-empty -m "chore: start claude-md bootstrap redesign branch"
```

---

## Task 2: Write `docs/claude-md-required.md`

This is the initial user-message content for an interactive `claude` session. It instructs Claude to configure the `<!-- ccst-required: start/end -->` block in `~/.claude/CLAUDE.md`.

**Files:**
- Create: `docs/claude-md-required.md`

- [ ] **Step 2.1: Write the file**

The file must cover, in order:

**Preamble** — verify CCST is installed:
```
Run: ccst doctor
If any component fails, stop and ask the user to fix it before continuing.
```

**Read current CLAUDE.md** — note any existing `<!-- ccst-required -->` block (idempotent replace, not append).

**Propose 8-digit gate additions:**

The proposed CLAUDE.md block under `### 8-digit confirmation gate`:
```
Always use the `generate-8digit-code` skill when you need a confirmation
code for a gated action. Never invent or guess a number yourself.
LLMs are not random number generators — model-generated numbers are
predictable and biased, which defeats the purpose of the gate.

When proposing a gated action: run the skill, then say exactly:
"Respond with `NNNNNNNN` to confirm."
Only proceed once the user replies with exactly that string.
```

**Interactive: ask which actions to gate.** Propose defaults:
1. Pushing commits to a remote repository (`git push`)
2. Force-pushing to any branch
3. Merging or landing a pull request
4. Deleting a local or remote branch
5. Financial transactions (purchases, orders)
6. Sending external messages (email, WhatsApp, Telegram, Slack to real people)
7. Deleting files or directories with `rm -rf` on paths outside the current project
8. Running `DELETE`/`DROP` SQL on a non-test database

Ask the user: which to keep, and any additions. Wait for response before writing.

**Propose session workspace additions** (present as a block, accept/reject together):

Under `### Session workspace`:
```
Sessions started with `ccd` create two directories:
- `cc-sessions/<tag>/working/` — scratch files, notes, WORKLOG.md
- `cc-sessions/<tag>/out/` — deliverables to keep or hand off

Keep a `WORKLOG.md` in `working/` as an append-only log of decisions,
blockers, and progress. The `edit-write-audit` hook auto-stages it;
the `session-end` hook will warn if it goes stale.
```

Under `### Agent workspace`:
```
Every Agent() dispatch creates a folder inside the session directory:
  cc-sessions/<session-tag>/agents/<session-tag>--<task-slug>/

Inside the agent folder, the agent MUST write:
- `prompt.md` — exact prompt given to the agent, written BEFORE the agent starts
- `WORKLOG.md` — append-only log of what the agent did, decisions, blockers
- Output files — deliverables; large content must NOT be returned inline

Tell the agent the path explicitly in the prompt:
"Write all outputs to `cc-sessions/<tag>/agents/<tag>--<slug>/`."

Every agent prompt must begin with the current session tag followed by `: `:
  <session-tag>: <rest of prompt>
```

**Write idempotently** using sentinels:
```
<!-- ccst-required: start -->
...
<!-- ccst-required: end -->
```
If sentinels already present: replace the block. If absent: append.

**Confirm** to the user: path written, lines in block, gated actions chosen.

- [ ] **Step 2.2: Verify the file renders correctly (spot-check)**

```bash
wc -l docs/claude-md-required.md
grep -c "ccst-required" docs/claude-md-required.md  # should be 2
```

Expected: file exists, at least 60 lines, exactly 2 sentinel references.

- [ ] **Step 2.3: Commit**

```bash
git add docs/claude-md-required.md
git commit -m "docs: add claude-md-required.md prompt (8-digit gate + session workspace)"
```

---

## Task 3: Write `docs/claude-md-recommendations.md`

Interactive prompt for the recommendations block. No personal name references ("the user", not "Chris"). Model names framed as "current defaults — verify against CCST docs."

**Files:**
- Create: `docs/claude-md-recommendations.md`

- [ ] **Step 3.1: Write the file**

**Preamble** — check for `<!-- ccst-required -->` block (should already exist; if not, tell user to run `claude-md-required.md` first).

**Read current CLAUDE.md** — note any existing `<!-- ccst-recommendations -->` block.

**Present the proposed block** as a whole; ask user to accept or reject. Offer to omit the orchestrator-executor-critic-assessor section if they find it too prescriptive (it's the most opinionated part).

The proposed `<!-- ccst-recommendations: start/end -->` block:

```markdown
## CCST — session management

Use `ccs` (or `ccl`) to list sessions, `ccr <fragment>` to resume,
`ccd <tag>` to start a new one.
Do not start new Claude Code sessions from inside a running session.

## CCST — using agents effectively

Use agents proactively when a sub-task is:
- Clear and self-contained
- Requires limited context
- Benefits from parallel execution or its own audit trail

Do NOT dispatch agents when:
- The task needs ongoing back-and-forth with the user
- You do not yet know what success looks like
- The work is trivially fast (single file read, grep, or short bash call)

**Context break-even:** if a sub-task would consume more than ~5% of the
current session's context window, dispatch it to a fresh agent rather than
doing it inline. Below that threshold, stay inline.

### Foreground vs background

- **Foreground** (default): agent's output is needed before the next step.
- **Background** (`run_in_background: true`): you have independent work to
  do in parallel, or the agent is long-running.

Never poll a background agent — the harness notifies on completion.

### Model selection

- **Default: Sonnet (current latest)** — clear, well-scoped tasks.
- **Opus** — substantial design, ambiguity, or complex cross-cutting reasoning.

State the model choice in the agent prompt's first line so it appears
in `prompt.md` for audit. Check CCST docs for current model identifiers.

### Parallel dispatch

Dispatch multiple independent agents in a single message (multiple Agent
tool-use blocks) so they run concurrently. Sequential dispatch wastes
wall-clock time and burns the main session's prompt cache.

### Orchestrator-executor-critic-assessor pattern

For non-trivial work where quality matters more than speed:

1. **Orchestrator** — defines the task, writes a quality rubric, runs the loop.
2. **Executor** (Sonnet) — produces or improves candidate output.
3. **Critic** (Sonnet or Opus) — challenges the output; finds problems, does not fix them.
4. **Assessor** (Opus) — decides which criticisms are valid; scores against rubric;
   returns `converged`, `iterate`, or `stop`.

Loop until converged, iteration cap hit (default 5), or pragmatic stop
(reason documented in orchestrator WORKLOG.md).

Each role gets its own agent folder per iteration. Use this pattern only
when warranted — it is expensive.

## CCST — session conduct

When a message contains multiple unrelated tasks, flag this and propose
splitting into separate sessions before starting any work. Unrelated means
tasks that share no context — completing one does not feed into the other.

## CCST — task list sharing

Sessions started with `ccd` in the same project automatically share a task
list (`CLAUDE_CODE_TASK_LIST_ID`). Use this to hand off tasks between sessions
in the same project without re-establishing context.
```

**Write idempotently** using `<!-- ccst-recommendations: start/end -->`.

- [ ] **Step 3.2: Verify**

```bash
wc -l docs/claude-md-recommendations.md
grep -c "ccst-recommendations" docs/claude-md-recommendations.md  # should be 2
grep "Chris" docs/claude-md-recommendations.md  # should return nothing
```

- [ ] **Step 3.3: Commit**

```bash
git add docs/claude-md-recommendations.md
git commit -m "docs: add claude-md-recommendations.md prompt (agent practices + session conduct)"
```

---

## Task 4: Write `docs/claude-md-check.md`

Oneshot prompt — used with `claude -p "$(cat ...)"`. Detection only, no writes.

**Files:**
- Create: `docs/claude-md-check.md`

- [ ] **Step 4.1: Write the file**

Content:
```markdown
Read ~/.claude/CLAUDE.md in full, including the content of any @-referenced
files. Then report:

1. **Duplications** — the same rule or guidance stated more than once (even
   with compatible wording). List each pair with the section names.
2. **Contradictions** — two sections that give conflicting instructions. List
   each conflict with the section names and the conflicting statements.
3. **Stale references** — mentions of tools, file paths, skills, or commands
   that do not appear to exist (e.g. a skill name that does not match any
   file in ~/.claude/skills/).
4. **Conflicts with CCST blocks** — anything outside the `<!-- ccst-required -->`
   and `<!-- ccst-recommendations -->` blocks that contradicts guidance inside
   them.

Format your report as a bulleted list, grouped by type. If a category has
no issues, say "None found."

This is detection only. Do not modify any files.
```

- [ ] **Step 4.2: Verify**

```bash
wc -l docs/claude-md-check.md
grep -i "do not modify" docs/claude-md-check.md  # safety check
```

- [ ] **Step 4.3: Commit**

```bash
git add docs/claude-md-check.md
git commit -m "docs: add claude-md-check.md prompt (contradiction detection)"
```

---

## Task 5: Write structural tests

These tests verify the prompt files exist, have the right markers, and are referenced by the install script. They prevent the classic rename-without-updating-reference failure.

**Files:**
- Create: `tests/test_claude_md_prompts.py`

- [ ] **Step 5.1: Write failing tests first**

```python
# tests/test_claude_md_prompts.py
"""Structural tests for CLAUDE.md bootstrap prompt files."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOCS = REPO_ROOT / "docs"
INSTALL_SCRIPT = REPO_ROOT / "install-everything.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestPromptFilesExist:
    def test_required_prompt_exists(self):
        assert (DOCS / "claude-md-required.md").exists()

    def test_recommendations_prompt_exists(self):
        assert (DOCS / "claude-md-recommendations.md").exists()

    def test_check_prompt_exists(self):
        assert (DOCS / "claude-md-check.md").exists()

    def test_old_bootstrap_removed(self):
        assert not (DOCS / "global-claude-md-bootstrap-prompt.md").exists(), (
            "Old bootstrap prompt should have been removed"
        )


class TestSentinelMarkers:
    def test_required_has_start_sentinel(self):
        text = _read(DOCS / "claude-md-required.md")
        assert "ccst-required: start" in text

    def test_required_has_end_sentinel(self):
        text = _read(DOCS / "claude-md-required.md")
        assert "ccst-required: end" in text

    def test_recommendations_has_start_sentinel(self):
        text = _read(DOCS / "claude-md-recommendations.md")
        assert "ccst-recommendations: start" in text

    def test_recommendations_has_end_sentinel(self):
        text = _read(DOCS / "claude-md-recommendations.md")
        assert "ccst-recommendations: end" in text

    def test_check_is_detection_only(self):
        text = _read(DOCS / "claude-md-check.md")
        assert "do not modify" in text.lower()


class TestNoPersonalReferences:
    def test_required_no_chris(self):
        text = _read(DOCS / "claude-md-required.md")
        assert "Chris" not in text, "Prompt must not contain personal name 'Chris'"

    def test_recommendations_no_chris(self):
        text = _read(DOCS / "claude-md-recommendations.md")
        assert "Chris" not in text, "Prompt must not contain personal name 'Chris'"

    def test_check_no_chris(self):
        text = _read(DOCS / "claude-md-check.md")
        assert "Chris" not in text


class TestInstallScriptReferences:
    def test_install_references_required_prompt(self):
        text = _read(INSTALL_SCRIPT)
        assert "claude-md-required.md" in text

    def test_install_references_recommendations_prompt(self):
        text = _read(INSTALL_SCRIPT)
        assert "claude-md-recommendations.md" in text

    def test_install_references_check_prompt(self):
        text = _read(INSTALL_SCRIPT)
        assert "claude-md-check.md" in text

    def test_install_script_claude_command_syntax(self):
        """Verify the interactive claude launch uses the correct shell syntax."""
        text = _read(INSTALL_SCRIPT)
        assert 'claude "$(cat "$REPO_DIR/docs/claude-md-required.md")"' in text
        assert 'claude "$(cat "$REPO_DIR/docs/claude-md-recommendations.md")"' in text
```

- [ ] **Step 5.2: Run tests — verify they fail for the right reasons**

```bash
uv run pytest tests/test_claude_md_prompts.py -v
```

Expected: all tests fail (files not yet created / install script not yet updated).

- [ ] **Step 5.3: Commit the test file**

```bash
git add tests/test_claude_md_prompts.py
git commit -m "test: add structural tests for claude-md prompt files"
```

*Tasks 2–4 above make the existence/sentinel/personal-name tests pass. Tasks 6–7 below make the install-script reference tests pass.*

---

## Task 6: Delete old bootstrap prompt

**Files:**
- Delete: `docs/global-claude-md-bootstrap-prompt.md`

- [ ] **Step 6.1: Grep for any remaining references**

```bash
grep -r "global-claude-md-bootstrap-prompt" . --include="*.md" --include="*.sh" --include="*.py" --include="*.txt"
```

Note every file found. Update or remove each reference as appropriate before deleting.

- [ ] **Step 6.2: Delete the file**

```bash
git rm docs/global-claude-md-bootstrap-prompt.md
```

- [ ] **Step 6.3: Run full test suite to confirm nothing broke**

```bash
uv run pytest -q
```

- [ ] **Step 6.4: Commit**

```bash
git commit -m "docs: remove global-claude-md-bootstrap-prompt.md (superseded by three focused prompts)"
```

---

## Task 7: Update `install-everything.sh`

Add steps 6, 7, 8. Renumber existing step 5 (health check) to step 8. Steps 6 and 7 offer to launch the sessions interactively; step 8 (check) runs with `-p` and prints to stdout.

**Files:**
- Modify: `install-everything.sh`

- [ ] **Step 7.1: Update the script**

Change the existing step label from `"5/5  Health check"` to `"8/8  Health check"`.

Add before the health-check step:

```bash
# ── Step 6: Required CLAUDE.md configuration ─────────────────────────────────
step "6/8  Configure ~/.claude/CLAUDE.md (required)"
echo ""
echo "  This step configures your global CLAUDE.md for the CCST confirmation"
echo "  gate and session workspace convention. Without it, the confirm-8digit"
echo "  hook and generate-8digit-code skill will not work correctly, and Claude"
echo "  Code sessions will not know about the cc-sessions/ workspace layout."
echo ""
echo "  Command to run:"
echo "    claude \"\$(cat $REPO_DIR/docs/claude-md-required.md)\""
echo ""
read -rp "  Launch now? [Y/n] " _launch_required
if [[ "${_launch_required,,}" != "n" ]]; then
    claude "$(cat "$REPO_DIR/docs/claude-md-required.md")"
else
    echo "  Skipped. Run the command above manually before using CCST."
fi

# ── Step 7: Recommended CLAUDE.md additions ──────────────────────────────────
step "7/8  Configure ~/.claude/CLAUDE.md (recommendations)"
echo ""
echo "  This step adds agent-practice and session-conduct guidance to your"
echo "  CLAUDE.md (proactive agents, model selection, orchestration pattern,"
echo "  session conduct, task-list sharing)."
echo ""
echo "  Command to run:"
echo "    claude \"\$(cat $REPO_DIR/docs/claude-md-recommendations.md)\""
echo ""
read -rp "  Launch now? [Y/n] " _launch_recs
if [[ "${_launch_recs,,}" != "n" ]]; then
    claude "$(cat "$REPO_DIR/docs/claude-md-recommendations.md")"
else
    echo "  Skipped. Run the command above manually when ready."
fi
```

And add a new step after all configuration, before health check:

```bash
# ── Step 7.5: CLAUDE.md contradiction check ──────────────────────────────────
step "7.5/8  Check ~/.claude/CLAUDE.md for contradictions"
echo ""
echo "  Running a quick check for duplications or contradictions in your"
echo "  CLAUDE.md after the changes made in steps 6 and 7."
echo ""
claude -p "$(cat "$REPO_DIR/docs/claude-md-check.md")"
```

> **Note on step numbering:** Use 6, 7, 7.5, 8 rather than renumbering heavily — the existing 5-step structure is referenced in existing docs and by users' muscle memory. A gap is fine; renumbering everything would require more doc churn.

- [ ] **Step 7.2: Run tests**

```bash
uv run pytest tests/test_claude_md_prompts.py -v -k "install"
```

Expected: the three `TestInstallScriptReferences` tests pass.

- [ ] **Step 7.3: Manually verify the script is valid bash**

```bash
bash -n install-everything.sh
```

Expected: no errors.

- [ ] **Step 7.4: Run the full test suite**

```bash
uv run pytest -q
```

- [ ] **Step 7.5: Commit**

```bash
git add install-everything.sh
git commit -m "feat(install): add CLAUDE.md configuration steps (required + recommendations + check)"
```

---

## Task 8: Update README.md

Three areas to update:
1. Install section: reference the 4-step process correctly (steps 6/7/7.5 in the easiest path; manual path steps 4/5/6)
2. New "Package groupings" subsection under "Bundled skills"/"Hook library" explaining the three all-or-nothing packs
3. Update "Configure your global CLAUDE.md" section to reference the new prompt files

**Files:**
- Modify: `README.md`

- [ ] **Step 8.1: Update the "Easiest path" blockquote**

Replace the current note about step 4 with the updated step numbering:

Old:
```
> **Note:** `install-everything.sh` handles steps 1–3 of setup. Step 4 — adding CCST guidance to your global `~/.claude/CLAUDE.md` — is interactive and must be run separately afterwards. See [Configure your global CLAUDE.md](#configure-your-global-claudemd) below.
```

New:
```
> **Note:** `install-everything.sh` runs all setup steps including interactive CLAUDE.md configuration (steps 6, 7, and a contradiction check). Steps 6 and 7 launch interactive Claude Code sessions — you can accept or skip each one. If you skip them, see [Configure your global CLAUDE.md](#configure-your-global-claudemd) for the manual commands.
```

- [ ] **Step 8.2: Update the "Manual path" code block**

Add steps 4, 5, 6 after `ccst doctor`. Note: these are numbered 4/5/6 in the manual path (where steps 1–3 are the install/skills/hooks commands). In `install-everything.sh` they appear as steps 6, 7, 7.5 because the script has two additional pre-numbered steps (shell helper and health check). Add a comment in the code block so users are not confused when they compare the two.

```sh
# 4. Configure CLAUDE.md — required (8-digit gate + session workspace)
#    This launches an interactive Claude Code session. Accept the proposed
#    changes to enable the confirmation gate and session workspace convention.
claude "$(cat ~/repos/claude-code-session-tools/docs/claude-md-required.md)"

# 5. Configure CLAUDE.md — recommendations (agent practices + session conduct)
#    Optional but recommended. Accept or reject the proposed block.
claude "$(cat ~/repos/claude-code-session-tools/docs/claude-md-recommendations.md)"

# 6. Check CLAUDE.md for contradictions (detection only)
claude -p "$(cat ~/repos/claude-code-session-tools/docs/claude-md-check.md)"
```

- [ ] **Step 8.3: Add "Package groupings" section**

After the three-section table (CLIs / Skills / Hooks) and before "See [CHANGELOG.md]", add:

```markdown
### Hook and skill packages

Hooks and skills are designed as cohesive packages, not à la carte choices. Installing only some of a package leaves CCST in a partially-functional state.

| Package | Components | CLAUDE.md needed? |
|---|---|---|
| **Mechanical safety** | `session-tag` + `prompt-guard` + `bash-security-review` hooks | No — fully automatic |
| **Confirmation gate** | `confirm-8digit` hook + `generate-8digit-code` skill + required CLAUDE.md (8-digit section) | Yes — hook is reactive without it; skill generates wrong output |
| **Session workspace** | `edit-write-audit` + `session-end` hooks + all five navigation/management skills + required CLAUDE.md (session workspace section) | Yes — convention has to be established before hooks and skills are useful |

`ccst skills install` and `ccst hooks install` install the components. The interactive CLAUDE.md steps (run during `install-everything.sh` or manually) complete the packages.
```

- [ ] **Step 8.4: Update "Configure your global CLAUDE.md" section**

Replace the current body with content that references the three prompt files explicitly and removes the outdated `global-claude-md-bootstrap-prompt.md` reference. The section should explain:
- Step 6 (`claude-md-required.md`): what it configures, why required
- Step 7 (`claude-md-recommendations.md`): what it configures, why recommended
- Step 7.5 (`claude-md-check.md`): what it does, detection only

Keep the "manual additions" bullet list but update it to mention the new prompt file paths.

- [ ] **Step 8.5: Run full test suite**

```bash
uv run pytest -q
```

- [ ] **Step 8.6: Commit**

```bash
git add README.md
git commit -m "docs(readme): update install process for three-prompt CLAUDE.md setup; add package groupings"
```

---

## Task 9: Final checks and PR

- [ ] **Step 9.1: Run full test suite one more time**

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 9.2: Check no remaining references to old bootstrap**

```bash
grep -r "global-claude-md-bootstrap-prompt" . --include="*.md" --include="*.sh" --include="*.py"
```

Expected: no output.

- [ ] **Step 9.3: Check no personal names in new prompt files**

```bash
grep -in "chris\|fogelberg\|katie\|cfogelberg" \
  docs/claude-md-required.md \
  docs/claude-md-recommendations.md \
  docs/claude-md-check.md
```

Expected: no output.

- [ ] **Step 9.4: Manual smoke test of install script syntax**

```bash
bash -n install-everything.sh
```

- [ ] **Step 9.5: Open PR**

```bash
git push -u origin f/20260517-claude-md-bootstrap-redesign
gh pr create \
  --title "feat: redesign CLAUDE.md bootstrap as three focused prompts" \
  --body "$(cat <<'EOF'
## Summary

- Replaces `docs/global-claude-md-bootstrap-prompt.md` with three focused prompts: `claude-md-required.md` (8-digit gate + session workspace), `claude-md-recommendations.md` (agent practices + session conduct), `claude-md-check.md` (contradiction detection)
- Wires all three into `install-everything.sh` as steps 6, 7, 7.5 with interactive Y/n prompts
- Documents the three all-or-nothing packages (mechanical safety / confirmation gate / session workspace) in README
- Updates manual install path with explicit step 4/5/6 commands
- Adds structural tests verifying prompt files, sentinel markers, and install script references

## Test plan

- [ ] `uv run pytest -q` passes
- [ ] `bash -n install-everything.sh` passes
- [ ] Manually run `claude "$(cat docs/claude-md-required.md)"` in a clean test environment and verify interactive flow
- [ ] Verify sentinel markers appear correctly in a test CLAUDE.md
- [ ] Verify check prompt runs cleanly with `claude -p "$(cat docs/claude-md-check.md)"`
EOF
)"
```

---

## Open questions / out of scope

- **"Messages file" for inter-session communication** (mentioned by user): no current CCST convention exists for this. Deferred to a future release.
- **Model name currency**: prompt files say "current latest Sonnet/Opus — check CCST docs." A future release could auto-detect from `ccst doctor`.
- **Remediation in check step**: the check prompt is detection-only by design. Auto-remediation is explicitly out of scope.
- **`ccst claude-md` subcommand**: intentionally not added. CLAUDE.md configuration stays as interactive Claude sessions to prevent partial/inconsistent setup.
