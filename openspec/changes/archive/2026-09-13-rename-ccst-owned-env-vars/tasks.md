## 1. Rename the eleven env vars (mechanical, no behavior change)

- [x] 1.1 In `src/hooks/*.py` and `src/cc_session_tools/{cli/ccst.py,lib/telemetry_store.py,lib/scheduler/ledger.py}` and
      `src/cc_session_tools/skills/update-command-cache/scripts/update_command_cache.py`, rename
      every `CCCS_ENFORCE_8DIGIT`, `CCCS_MARKERS_DIR`, `CCCS_ALLOW_STALE_WORKLOG`,
      `CCCS_USE_COMMAND_CACHE`, `CCCS_CACHE_DB`, `CCCS_CLAUDE_BIN`, `CCCS_REVIEW_MODEL`,
      `CCCS_REVIEW_TIMEOUT`, `CCCS_HOOKS_DIR`, `CCCS_FIRES_ACCESS`,
      `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` reference (code and docstrings/comments) to its `CCST_`
      equivalent. Leave `CCCS_ALLOW_MAIN` untouched (parsed by a CCCS-authored bash script, not
      CCST). Verify: `grep -rln "CCCS_\(ENFORCE_8DIGIT\|MARKERS_DIR\|ALLOW_STALE_WORKLOG\|USE_COMMAND_CACHE\|CACHE_DB\|CLAUDE_BIN\|REVIEW_MODEL\|REVIEW_TIMEOUT\|HOOKS_DIR\|FIRES_ACCESS\|CONFIRM_8DIGIT_GATED_TOOLS\)" src/`
      returns nothing.
- [x] 1.2 Apply the same rename across every test file that sets or asserts on one of these
      names. Verify: `uv run pytest -q` passes (full suite).
- [x] 1.3 Update `openspec/specs/notify/confirm-8digit-gated-tools-config/spec.md`'s requirement
      text to name `CCST_CONFIRM_8DIGIT_GATED_TOOLS` (done via this change's own delta spec sync
      at archive time, not a separate manual edit).

## 2. Full verification and release

- [x] 2.1 Run `uv run pytest -q` (full suite) and `uv run mypy` on every touched file; confirm
      both are clean.
- [x] 2.2 Update `CHANGELOG.md` with the full old-name -> new-name mapping for all eleven
      variables, and bump `pyproject.toml`'s version as a patch release (naming correction to a
      less-than-24-hours-old configuration surface, not a compatibility break in practice); run
      `uv lock` and commit the regenerated `uv.lock` in the same commit.

## 3. Companion change in `claude-code-config-sync` (separate repo)

- [ ] 3.1 In `claude-code-config-sync`, rename `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` to
      `CCST_CONFIRM_8DIGIT_GATED_TOOLS` in `config/settings.json`'s `env` block (whichever of
      PR #107 or `main` has it by the time this task runs) and update `README.md`'s wording
      accordingly.
- [ ] 3.2 Update the live `~/.claude/settings.json` on this machine to use the new name.
