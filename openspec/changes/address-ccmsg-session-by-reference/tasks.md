## 1. Resolver

- [ ] 1.1 Add failing tests for the resolver (canonical uuid passes through; unique name; unique prefix; name wins over prefix; zero matches; several matches listing candidates; prefix under 8 characters; placeholder text; same uuid under two project directories counts once)
- [ ] 1.2 Add `find_by_uuid_prefix` to `src/cc_session_tools/lib/sessions_db.py` with a test against a temporary database
- [ ] 1.3 Add `src/cc_session_tools/lib/messaging/session_ref.py` with `resolve_session_ref`, turning the tests green

## 2. ccmsg

- [ ] 2.1 Add failing CLI tests: send by name stores the full uuid; send with an unresolvable reference exits 2 and creates no message; `read` prints `from_uuid:` after `from:`
- [ ] 2.2 Call the resolver from `_resolve_recipient` for `--to-session`; update the `--to-session` help text (`UUID` to `UUID|NAME|PREFIX`)
- [ ] 2.3 Print `from_uuid:` in `_cmd_read`
- [ ] 2.4 Update existing tests in `tests/messaging/test_ccmsg_cli.py` that pass `target-uuid`/`u2` to use a canonical uuid

## 3. Skill

- [ ] 3.1 Update `src/cc_session_tools/skills/send-session-message/SKILL.md` to document the three accepted forms, the error cases and the `from_uuid:` line; keep the file free of personal identifiers

## 4. Release

- [ ] 4.1 Run the full check suite (`uv run pytest -q`, `uv run mypy src`) and confirm exit 0
- [ ] 4.2 Update `CHANGELOG.md` and bump `pyproject.toml` to the next minor version; run `uv lock` and commit `uv.lock` in the same commit
- [ ] 4.3 Sync the delta spec into `openspec/specs/` and archive the change before shipping
- [ ] 4.4 Push the branch, open the PR with a real summary, run `do-code-review` and post its findings as a PR comment; report the PR URL and stop (do not merge)
