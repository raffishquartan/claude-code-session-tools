## 1. Status definitions in help

- [x] 1.1 Failing tests in `tests/messaging/test_ccmsg_cli.py`: `ccmsg --help` and `ccmsg list --help` each contain `sent`, `read`, `claimed`, `archived`, the 14-day rule and `unarchive`, and the two blocks are identical
- [x] 1.2 Add `STATUS_HELP` to `cli/ccmsg.py` and use it as the epilog of the top-level and `list` parsers

## 2. Body-file guidance

- [x] 2.1 Failing test: `ccmsg send --help` recommends `--body-file` and mentions hooks
- [x] 2.2 Update `send` help text and `skills/send-session-message/SKILL.md` (write the body under the session's `working/` folder)

## 3. Path recipients for `--to-project`

- [x] 3.1 Failing tests: absolute path, `~` path, subfolder (`cc-sessions/<tag>`), unknown path exits non-zero and stores nothing, plain name unchanged, `store.project_dir_for_path` unit cases
- [x] 3.2 Add `store.project_dir_for_path` and wire it into `_resolve_recipient` / `_resolve_to_partition`
- [x] 3.3 Update `--to-project` help, `skills/send-session-message/SKILL.md` and `README.md`

## 4. ccsched skill note

- [x] 4.1 Add a two-line note to `skills/manage-recurring-cc-jobs-using-ccsched/SKILL.md`: no one-shot form, use `/schedule` for a single future-dated reminder

## 5. Release

- [x] 5.1 Run `uv run pytest -q`, `uv run mypy src`, configured lint, and `openspec validate --strict` for this change
- [x] 5.2 Update `CHANGELOG.md` (Keep a Changelog) with a 4.7.0 entry dated 2026-11-25, bump `pyproject.toml` to 4.7.0, run `uv lock` and commit `uv.lock` in the same commit
- [x] 5.3 Sync the delta specs into `openspec/specs/` and archive the change
