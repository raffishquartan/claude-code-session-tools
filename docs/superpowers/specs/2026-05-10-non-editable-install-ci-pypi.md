# Spec: Non-editable install, CI modernisation, PyPI publishing

**Date:** 2026-05-10
**Version bump:** 0.5.0 → 0.6.0

## Problem

`uv tool install --editable .` records the absolute path of the source directory in a
`.pth` file. If that source directory is a git worktree that is later deleted (after a PR
merges), the `.pth` path becomes invalid and all four CLIs (ccd, ccr, ccs,
claude-code-usage) fail at import time with `ModuleNotFoundError: No module named
'cc_session_tools'`.

This happened in practice after the `f-session-metadata` worktree was removed following
PR #7 merge on 2026-05-10.

## Solution

Switch the canonical install to non-editable (`uv tool install .`). Non-editable builds a
wheel and copies source into the venv's `site-packages` - no `.pth` pointer, immune to
worktree lifecycle. Modernise CI to use uv, test against Python 3.10-3.13, and add an
install-verification job. Add a release workflow that publishes to PyPI and GitHub
Releases on version tags.

## Changes

### 1. `.github/workflows/ci.yml` - replace existing

Two jobs:

**`test`** (matrix: Python 3.10, 3.11, 3.12, 3.13)
- `astral-sh/setup-uv@v4`
- `uv pip install ".[dev]"` (non-editable, installs package + pytest)
- `uv run pytest -q`

**`install-check`** (Python 3.13 only)
- `astral-sh/setup-uv@v4`
- `uv tool install .`
- Assert `ccd --version`, `ccr --version`, `ccs --version`, `claude-code-usage --version`
  all exit 0

### 2. `pyproject.toml`

- Version: `0.5.0` → `0.6.0`
- Add `[project.optional-dependencies] dev = ["pytest>=7"]`

### 3. `README.md`

- **Installation section**: primary path becomes `pipx install cc-session-tools` /
  `uv tool install cc-session-tools`. Remove the `pipx install git+...` form (PyPI is
  cleaner). Keep `git+...` as a fallback note.
- **Development section**: `pip install -e .` → `uv sync && uv run pytest`. Add callout:
  never `uv tool install` from a worktree; use `uv run` to test changes in-worktree.
- Update CI line to mention Python 3.10-3.13 and install-check job.

### 4. `docs/design.md`

Update one sentence: editable install → non-editable for production; `uv sync` for local
dev.

### 5. `skills/claude-usage/SKILL.md`

- Change install block: `uv tool install --editable .` → `uv tool install ~/repos/claude-code-session-tools`
- Add WARNING: never run `uv tool install` from inside a git worktree.

### 6. `.claude/CLAUDE.md` (new)

Dev workflow doc:
- Feature branches in worktrees under `.worktrees/`
- Test with `uv run pytest` / `uv run python -m cc_session_tools.cli.ccd` - never
  `uv tool install` from a worktree
- After merging: `uv tool install ~/repos/claude-code-session-tools` to update global
  install
- Version policy: minor for interface/install changes, patch for bug fixes

### 7. `.github/workflows/release.yml` (new)

Trigger: `push` to tags matching `v*`.

Steps (in sequence):
1. **Build**: `uv build` → `dist/*.whl` + `dist/*.tar.gz`
2. **GitHub Release**: `softprops/action-gh-release` - creates release, attaches
   wheel + sdist as assets
3. **PyPI publish**: `pypa/gh-action-pypi-publish` via OIDC trusted publisher (no
   stored secrets). Requires one-time Trusted Publisher setup on pypi.org.

The release workflow is created in this PR but will not fire until a `v*` tag is pushed.
PyPI trusted publisher must be configured before the first tag is pushed.

## Steps to fire the first release (after PR merges)

1. Log in to pypi.org → Your projects → Add project → Publishing → Add a new pending
   trusted publisher:
   - PyPI project name: `cc-session-tools`
   - Owner: `raffishquartan`
   - Repository: `claude-code-session-tools`
   - Workflow filename: `release.yml`
   - Environment: (leave blank)
2. `git tag v0.6.0 && git push --tags`
3. Create a GitHub Release from tag `v0.6.0` (or let the workflow create it via
   `softprops/action-gh-release`)
4. The workflow fires: builds, creates release, publishes to PyPI
