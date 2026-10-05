## 1. Key file library

- [x] 1.1 Write failing tests for `lib/api_keys.py` (parse, comments/blank lines, duplicate/malformed line, missing file, empty file, mode 0644 refused, `CCST_API_KEYS_FILE` override, errors never contain key values), then implement; verify `uv run pytest tests/test_api_keys.py -q` passes
- [x] 1.2 Add `src/cc_session_tools/config/api-keys.example` (comments + one commented fake example) and a test that it parses to zero entries and is included in the built wheel

## 2. Launchers

- [x] 2.1 Add `-k` and menu selection to `ccd.main(api_key=True)` with tests for named key, menu pick, cancel, unknown label, non-tty, dry-run hiding the key, and `ANTHROPIC_API_KEY` set / `CLAUDE_CODE_OAUTH_TOKEN` dropped in the launch env (monkeypatched `launch_claude`)
- [x] 2.2 Same for `ccr.main(api_key=True)` including the picker-then-key-menu ordering; verify existing `ccd`/`ccr` tests still pass
- [x] 2.3 Add `ccdapi`/`ccrapi` entry points to `pyproject.toml`; verify `CCST_NO_AUTO_SYNC=1 uv run ccdapi --help` and `ccrapi --help` work

## 3. Verification and docs

- [x] 3.1 Manually confirm with a real key that the launched `claude` bills the API key rather than the subscription; record any extra flag/env needed in design.md
- [x] 3.2 Update README (commands table, keys-file setup, template), `CHANGELOG.md` (Added, 3.11.0) and bump `pyproject.toml` to 3.11.0 with `uv lock`; verify `uv run pytest -q`, lint and type-check all exit 0
- [x] 3.3 Leave a handoff message for the `claude-code-config-sync` project (via `ccmsg`) describing the file path, format and template so cccs can manage the real file
