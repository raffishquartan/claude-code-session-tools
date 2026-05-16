# PII Scrub from Repository History

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the owner's first name (`chris`) and Windows username (`cfoge`) from all tracked files and git history, drop the now-unnecessary `lint-no-personal-paths` CI job, and add a PII policy to `~/.claude/coding-standards.md`.

**Architecture:** Fix the working tree first, commit, then rewrite all history with `git filter-repo --replace-text`. Force-push to remote. The `~/.claude/` organisation (move md files to a subdirectory) is independent and can be done before or after the git work.

**Tech Stack:** git, git-filter-repo (Python tool), uv, pytest, GitHub Actions CI

---

## Context and PII inventory

**What counts as PII here:**
- `/home/alice` — the owner's Linux home path
- `/mnt/c/Users/alice` — the owner's Windows OneDrive path
- `cfoge` — the Windows username fragment

**What does NOT need scrubbing:**
- `raffishquartan` — this is the public GitHub handle for the repo; removing it would break the package metadata and all install instructions.

**Current working-tree files with PII (all need fixing in Task 1):**

| File | PII | Fix |
|---|---|---|
| `.github/workflows/ci.yml` | `lint-no-personal-paths` job using `pat='/home/''chris'` | Remove entire job |
| `src/cccs_hooks/edit_write_audit.py:32` | `Path("/mnt/c/Users/alice/OneDrive")` | Remove this entry from `_DEFAULT_REPO_ROOTS` |
| `tests/test_parser.py:17,51` | `"/mnt/c/Users/alice/OneDrive/claude/oneshot"` | Replace `cfoge` → `alice` |
| `tests/test_schema.py:14,49` | `"/mnt/c/Users/alice/OneDrive/claude/oneshot"` | Replace `cfoge` → `alice` |
| `tests/test_session_tag.py:54` | `"/mnt/c/Users/alice/OneDrive/claude/oneshot"` | Replace `cfoge` → `alice` |

**Files only in git history (already clean in working tree, but need history rewrite):**
`docs/superpowers/plans/2026-05-10-ccx-improvements.md`, `skills/find-claude-code-session/SKILL.md`, `src/cccs_hooks/session_tag.py`, `src/cccs_hooks/transcript.py`, `tests/test_sessions.py`, `tests/test_telemetry.py`

---

## Pre-flight: land the pending rename commit

The current branch `f/20260515-oneshot-rename-skills-verb-first` is 1 commit ahead of `main` (`e15ab56 refactor: rename claude-usage skill to analyse-cc-usage`). Merge or PR that before starting the history rewrite so no work is orphaned.

- [ ] Check: `git log main..HEAD --oneline` — should show `e15ab56`
- [ ] Open PR for that branch and merge it, OR `git checkout main && git merge f/20260515-oneshot-rename-skills-verb-first`

All subsequent tasks run on `main`.

---

## Task 1: Fix all PII in the working tree

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `src/cccs_hooks/edit_write_audit.py`
- Modify: `tests/test_parser.py`
- Modify: `tests/test_schema.py`
- Modify: `tests/test_session_tag.py`

### 1a — Remove `lint-no-personal-paths` from ci.yml

- [ ] Open `.github/workflows/ci.yml`. Delete the entire `lint-no-personal-paths` job (the final job in the file, from `lint-no-personal-paths:` to end of file).

The job to delete looks like:
```yaml
  lint-no-personal-paths:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6.0.2
      - name: Fail if personal paths appear in tracked files
        run: |
          pat='/home/''chris'
          ! git grep -rn "$pat"
```

- [ ] Verify: `cat .github/workflows/ci.yml | grep -c "chris"` should return `0`.

### 1b — Remove personal path from `_DEFAULT_REPO_ROOTS`

- [ ] Open `src/cccs_hooks/edit_write_audit.py`. Change:

```python
_DEFAULT_REPO_ROOTS = [
    Path.home() / "repos",
    Path("/mnt/c/Users/alice/OneDrive"),
]
```

to:

```python
_DEFAULT_REPO_ROOTS = [
    Path.home() / "repos",
]
```

### 1c — Replace `cfoge` with `alice` in test fixture paths

All three replacements are in test helper strings that serve as example CWD paths. The convention already used elsewhere in the test suite is `/home/alice` and `/mnt/c/Users/alice/...`.

- [ ] In `tests/test_parser.py`, replace both occurrences:
  - `"/mnt/c/Users/alice/OneDrive/claude/oneshot"` → `"/mnt/c/Users/alice/OneDrive/claude/oneshot"`

- [ ] In `tests/test_schema.py`, replace both occurrences:
  - `"/mnt/c/Users/alice/OneDrive/claude/oneshot"` → `"/mnt/c/Users/alice/OneDrive/claude/oneshot"`

- [ ] In `tests/test_session_tag.py` line 54, replace:
  - `cwd = "/mnt/c/Users/alice/OneDrive/claude/oneshot"` → `cwd = "/mnt/c/Users/alice/OneDrive/claude/oneshot"`

### 1d — Verify working tree is clean

- [ ] Run: `git grep -rn "cfoge\|/home/alice"` — should return nothing.
- [ ] Run: `uv run pytest -q` — all tests must pass.

### 1e — Commit

```bash
git add .github/workflows/ci.yml \
        src/cccs_hooks/edit_write_audit.py \
        tests/test_parser.py \
        tests/test_schema.py \
        tests/test_session_tag.py
git commit -m "fix: remove personal identifiers from source, tests, and CI

Drop the lint-no-personal-paths CI job (the job definition itself
contained the identifier it was searching for, making it self-defeating).
Remove hardcoded personal Windows path from _DEFAULT_REPO_ROOTS.
Replace personal username in test fixture paths with neutral placeholder.

[Cld]"
```

---

## Task 2: Rewrite git history with git-filter-repo

`git filter-repo` replaces string literals in every blob in every commit, then re-signs all commit objects. This changes every commit SHA. The remote must be force-pushed afterwards.

**Files:**
- Create (temp): `/tmp/pii-replacements.txt`

### 2a — Install git-filter-repo if not present

- [ ] Run: `git filter-repo --version 2>/dev/null || pip install git-filter-repo`
- [ ] Confirm: `git filter-repo --version` prints a version number.

### 2b — Create the replacements file

- [ ] Create `/tmp/pii-replacements.txt` with this exact content (two lines):

```
/home/alice==>/home/alice
/mnt/c/Users/alice==>/mnt/c/Users/alice
```

Note: `==>` (three characters) is the `git filter-repo` delimiter, not `==`.

### 2c — Dry-run check (optional but recommended)

- [ ] Capture the list of blobs that will change:
```bash
git log --all --format="%H" | \
  xargs -I{} git diff-tree --no-commit-id -r {} 2>/dev/null | \
  awk '{print $4}' | sort -u | \
  xargs -I{} sh -c 'git cat-file blob {} 2>/dev/null | grep -l "cfoge\|/home/alice" && echo {}' 2>/dev/null | head -20
```

This shows which blob SHAs have PII — confirms we're replacing the right things.

### 2d — Run the history rewrite

**WARNING: this is irreversible on the local repo. Make sure the pre-flight commit (Task 1) is done first.**

```bash
git filter-repo --replace-text /tmp/pii-replacements.txt
```

- [ ] Wait for completion (should take under 30 seconds for this repo size).
- [ ] Confirm: `git log --oneline | head -5` — commit SHAs will all be different from before.

**Note on this plan document itself:** The replacements file will also mutate this plan document's own content in git history (e.g. `/home/alice` → `/home/alice` in the text above). That is acceptable; the plan is historical documentation. The live copy in the working tree will have its example strings rewritten but remains readable.

### 2e — Force-push tags

`git filter-repo` rewrites commit objects, so the five existing tags (`v0.7.0`, `v0.8.0`, `v0.9.0`, `v0.10.0`, `v0.10.1`) now point at the old (pre-rewrite) commit SHAs. They must be force-pushed so GitHub serves the rewritten objects:

```bash
git push origin --force --tags
```

- [ ] Confirm: the GitHub releases page still shows the five tags, and clicking through to the associated commits shows the new (rewritten) SHAs.

### 2f — Verify working tree is still clean after rewrite

- [ ] `git grep -rn "cfoge\|/home/alice"` — must return nothing.
- [ ] `uv run pytest -q` — all tests must still pass.

---

## Task 3: Force-push to remote and verify CI

After `git filter-repo`, the local `main` has diverged from `origin/main` (different SHAs). Force-push is required.

### 3a — Re-add remote (filter-repo removes it as a safety measure)

`git filter-repo` removes the remote to prevent accidental pushes. Re-add it:

```bash
git remote add origin https://github.com/raffishquartan/claude-code-session-tools.git
```

Verify: `git remote -v`

### 3b — Force-push main

```bash
git push --force origin main
```

- [ ] Confirm push succeeds.
- [ ] Open https://github.com/raffishquartan/claude-code-session-tools/commits/main and confirm the commit history looks correct (no cfoge/chris in any file).

### 3c — Delete or force-push any remaining feature branches

The current remote branches (besides `main`) are:
- `f/20260515-fix-personal-paths-in-session-tag`
- `f/20260515-oneshot-rename-skills-verb-first`
- `f/20260515-session-uuid-tag`

For each:
- [ ] Check if it is ahead of main: `git log main..origin/<branch> --oneline`
- [ ] If it has no unmerged commits (squash-merged or fully merged): delete it from remote
- [ ] If it has unmerged commits: force-push the local rewritten copy

```bash
# List what's ahead of main for each branch:
for b in f/20260515-fix-personal-paths-in-session-tag f/20260515-oneshot-rename-skills-verb-first f/20260515-session-uuid-tag; do
  echo "--- $b ---"
  git log main..origin/$b --oneline 2>/dev/null || echo "(not found locally)"
done

# Delete branches with no unmerged work:
git push origin --delete <branch-name>

# Force-push branches with unmerged work that should be preserved:
git push --force origin <local-branch>:<remote-branch>
```

### 3d — Monitor CI

- [ ] Open the Actions tab on GitHub for the force-pushed commit.
- [ ] Confirm `test` and `install-check` jobs pass on all matrix entries (ubuntu + macOS, Python 3.11/3.12/3.13).
- [ ] Confirm there is no `lint-no-personal-paths` job in the run (it should be gone).

---

## Task 4: Update ~/.claude/coding-standards.md

Add a PII prevention section so this never recurs.

**Files:**
- Modify: `~/.claude/coding-standards.md`

- [ ] Add the following section at the END of `~/.claude/coding-standards.md`, before the last `---` (or at the end of the Python section):

```markdown
### No personal identifiers in committed code

Personal identifiers — real names, usernames, email addresses, or filesystem paths that are specific to one machine or person — must never appear in committed source code, tests, docs, or CI scripts.

This includes:
- Home directory paths (`/home/<your-name>`, `C:\Users\<your-name>`, `/mnt/c/Users/<your-name>`)
- Personal email addresses
- Personal GitHub usernames (except where they are the legitimate identity of the repo, e.g. in LICENSE and pyproject.toml `authors`)

**Correct pattern for test fixture paths:** Use a clearly fictional placeholder (`/home/alice`, `/mnt/c/Users/alice/`, `/example/repos/project`).

**Correct pattern for default config:** Use `Path.home()` to derive the OS-portable home path at runtime. Never hardcode an absolute path that contains a real username.

**When you notice a violation:** Fix it in the working tree first, then assess whether the identifier needs scrubbing from git history. If the repo is public, assume yes.
```

- [ ] Save and verify: `grep -n "personal identifier" ~/.claude/coding-standards.md` returns the new section heading.

---

## Task 5: Move ~/.claude/ md files into a subdirectory

Tidying so `~/.claude/` holds only `CLAUDE.md` (the entrypoint) and the `claude-md-specifics/` folder holds the referenced detail files.

**Files:**
- Create dir: `~/.claude/claude-md-specifics/`
- Move: `~/.claude/coding-standards.md` → `~/.claude/claude-md-specifics/coding-standards.md`
- Move: `~/.claude/reference-people.md` → `~/.claude/claude-md-specifics/reference-people.md`
- Move: `~/.claude/writing-style.md` → `~/.claude/claude-md-specifics/writing-style.md`
- Modify: `~/.claude/CLAUDE.md` to reference the new paths

### 5a — Create subdirectory and move files

```bash
mkdir -p ~/.claude/claude-md-specifics
mv ~/.claude/coding-standards.md ~/.claude/claude-md-specifics/
mv ~/.claude/reference-people.md ~/.claude/claude-md-specifics/
mv ~/.claude/writing-style.md ~/.claude/claude-md-specifics/
```

- [ ] Verify: `ls ~/.claude/claude-md-specifics/` shows all three files.

### 5b — Update ~/.claude/CLAUDE.md

The file is currently empty. Add content that explicitly loads the moved files so Claude Code picks them up:

```markdown
@claude-md-specifics/coding-standards.md
@claude-md-specifics/reference-people.md
@claude-md-specifics/writing-style.md
```

- [ ] Verify that a new Claude Code session loads all three files by checking that coding-standards content appears in the system prompt context.

---

## Verification checklist

After all tasks:

- [ ] `git grep -rn "cfoge\|/home/alice"` in the repo returns nothing
- [ ] `uv run pytest -q` passes
- [ ] GitHub Actions CI passes (test + install-check on all matrix entries)
- [ ] No `lint-no-personal-paths` job exists in CI
- [ ] `git log --all -p | grep -E "cfoge|/home/alice"` returns nothing (confirms history is clean)
- [ ] `~/.claude/claude-md-specifics/coding-standards.md` exists and contains the new PII section
- [ ] `~/.claude/CLAUDE.md` references the three moved files
