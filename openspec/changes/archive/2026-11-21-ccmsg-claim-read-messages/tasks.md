## 1. Claim accepts read messages

- [x] 1.1 Failing tests in `tests/messaging/test_repository.py`: claim of a `read` session message, claim of a `read` project message then second claim fails, reader stamp unchanged, `claimed`/`archived` still rejected, `sent` still claimable
- [x] 1.2 Update `repository.claim` to accept `sent` and `read`, preserving the original reader stamp
- [x] 1.3 Race test: two concurrent claims of one `read` message, exactly one wins (extend `tests/messaging/test_repository_race.py`)

## 2. Digest hint and docs

- [x] 2.1 Failing test in `tests/messaging/test_service.py`: read-but-unhandled section tells the session to `ccmsg claim <id>` with real `--uuid` and `--session`; a claimed message drops out of the list
- [x] 2.2 Update the digest text in `service.py` and the `unhandled_read` docstring
- [x] 2.3 Update `ccmsg claim` help text, the global snippet in `lib/claude_md_install.py` and `skills/send-session-message/SKILL.md`
- [x] 2.4 Test for `ccmsg archive` of an unclaimed read message followed by `ccmsg unarchive`

## 3. Release

- [x] 3.1 Run the full test suite and `openspec validate --strict` for this change
- [x] 3.2 Update `CHANGELOG.md` (Keep a Changelog) and bump the minor version in `pyproject.toml`, run `uv lock` and commit `uv.lock`; version 4.5.0, following the rebased stack (4.2.1, 4.3.0, 4.4.0)
- [x] 3.3 Archive the change and sync its delta specs into `openspec/specs/`
