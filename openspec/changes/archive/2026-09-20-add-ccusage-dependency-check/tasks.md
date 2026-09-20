## 1. Skill instructions

- [x] 1.1 Rewrite `analyse-cc-usage/SKILL.md` "Setup on a new machine" step 4 into explicit steps: install bun (same command on macOS and Linux/WSL, with the `unzip` prerequisite and WSL-side-install note), verify `bun --version`, add bun's bin dir to `PATH` (note `~/.zshenv` for non-interactive shells), `bun add -g ccusage`, verify `ccusage --version`.
- [x] 1.2 Add a short "upgrading an existing machine" note pointing at `ccst doctor`'s `deps:*` checks.
- [x] 1.3 Update the README "`ccusage` (optional)" line to reference the doctor checks.

## 2. Lookup

- [x] 2.1 Add a single helper that resolves `ccusage` via `PATH` then `$BUN_INSTALL/bin` (default `~/.bun/bin`); use it from `ccusage_wrapper.py` and from the doctor check so the two cannot drift.
- [x] 2.2 Update the `CcusageNotInstalled` message.
- [x] 2.3 Tests: found on PATH; found only in bun bin dir; found nowhere.

## 3. Doctor checks

- [x] 3.1 Add `deps:bun` and `deps:ccusage` checks (OK / WARN-missing / WARN-not-on-PATH) and wire them into `ccst doctor`.
- [x] 3.2 Tests for each state, including that mute works per check name and that neither state ever produces FAIL.

## 4. Verification and release

- [x] 4.1 `uv run pytest -q` and every other configured check (lint, type-check) exits 0.
- [x] 4.2 Run `ccst doctor` from the worktree with `CCST_NO_AUTO_SYNC=1` and confirm the new checks render on this machine.
- [x] 4.3 Add the `CHANGELOG.md` entries under `[Unreleased]` (Keep a Changelog, `### Added` / `### Changed`). The `pyproject.toml` version bump (minor) and `uv lock` are deliberately deferred to the final task of the stacked `slim-security-review-and-script-allowlist` change, so one bump covers both changes and the second PR carries it.
