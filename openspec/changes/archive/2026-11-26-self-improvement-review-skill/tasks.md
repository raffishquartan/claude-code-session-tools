## 1. Record-review command

- [x] 1.1 `state.append_log_line` and `state.interactive_line`; the headless logger uses `append_log_line`; verify with state tests
- [x] 1.2 `ccst self-improvement record-review --mode B|C --findings N --applied N --deferred N`; verify with CLI tests (line format, `CCST_DATA_HOME`, negative or missing count rejected, line counts as the last interactive review)

## 2. Messages name the skill

- [x] 2.1 Remove `review_command()`, `review_skill_installed()` and `STATUS_COMMAND`; the nudge, escalation and `status` always name `/review-session-and-improve-claude-setup periodic`; update the nudge, signals and CLI tests
- [x] 2.2 Test that both not-set-up job summary lines name `ccst self-improvement init` and fit the scheduler's 1000-character capture

## 3. Bundled skill

- [x] 3.1 Add `src/cc_session_tools/skills/review-session-and-improve-claude-setup/SKILL.md` (ccst commands, no personal or machine-specific details)
- [x] 3.2 Tests: the skill is discovered and linked by `ccst skills install --apply` into a temporary target; a real directory of the same name makes it exit 1 and is left untouched; doctor reports it FAIL; the skill names only `ccst` commands that exist and contains no `.sh` script

## 4. Docs and release

- [x] 4.1 README (loop section and bundled skills list, upgrade step for an existing copied directory), CLI help
- [x] 4.2 CHANGELOG 4.12.0; bump `pyproject.toml` to 4.12.0 and run `uv lock` in the same commit
- [x] 4.3 `uv run pytest -q`, `uv run mypy src` and `openspec validate --specs` exit 0
