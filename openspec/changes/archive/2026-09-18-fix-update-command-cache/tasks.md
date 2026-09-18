## 1. Regression test (TDD)

- [x] 1.1 Add a subprocess-based test to `test_update_command_cache.py`:
      `test_script_runs_as_real_subprocess_without_bootstrap_error` — runs
      `[sys.executable, str(script_path), "list"]` via `subprocess.run`, with
      `CCST_FIRES_ACCESS=1`, `CCST_DATA_HOME=<tmp_path>` set in `env` (an empty
      `CCST_DATA_HOME` gives an empty telemetry.db, no real data touched), and asserts
      `returncode == 0` and `"ModuleNotFoundError" not in stderr`. Confirm this fails against the
      current script (`ModuleNotFoundError: No module named 'hooks'`) before making any source
      change. Verify: `uv run pytest -q src/cc_session_tools/skills/update-command-cache/tests -k
      subprocess` shows the new test failing for the expected reason.

## 2. Fix the script

- [x] 2.1 Remove the `_REPO_ROOT` / `sys.path.insert` bootstrap block from
      `update_command_cache.py`; import `hooks.cache` and `cc_session_tools.lib.telemetry_store`
      directly (no `# noqa: E402` needed once the imports move above any path-setup code, since
      there is none left). Verify: the new subprocess test from 1.1 now passes; run
      `uv run pytest -q src/cc_session_tools/skills/update-command-cache/tests` and confirm the
      whole file is green.
- [x] 2.2 Fix the module docstring's `Usage:` block: show the real subcommands (`list` / `remove
      <sha>` / `flip <sha> <verdict>` / `promote <sha> ...`) and state that the script must be run
      through an interpreter that has `cc-session-tools` installed (`uv run python3 <path>` in a
      checkout, or the installed tool's own venv interpreter — the same one `ccst` runs under —
      once installed via `uv tool install`), not a bare system `python3`. Verify: read the updated
      docstring against `main()`'s actual `argparse` subparsers for a literal match.

## 3. Fix `SKILL.md`

- [x] 3.1 Replace the `--list` / `--remove <sha>` / `--flip <sha> <verdict>` flag documentation
      with the real `list` / `remove <sha>` / `flip <sha> <verdict>` / `promote <sha> ...`
      subcommands, matching `main()`'s `argparse` subparsers exactly.
- [x] 3.2 Correct the cache DB path from `~/.cache/claude/logs/command-cache.db` to
      `~/.local/share/claude/command-cache.db` (overridable via `CCST_CACHE_DB`, per
      `hooks/cache.py`'s own docstring).
- [x] 3.3 Rewrite workflow step 4 to describe the actual, already-shipped behavior: the tool lists
      candidates by sha/fire-count/last-seen/sample-session (no plaintext command), and recovering
      the plaintext to eyeball before promoting is a manual step — walk the named sample session's
      transcript for the command that produced that hash — not an automated preview.
- [x] 3.4 Add a short note that `telemetry.db`'s `bash-security-review` rows can include synthetic
      test-fixture rows (`session_id` starting `test-session-`) mixed in with production fires, so
      a curation pass isn't surprised by one dominating the candidate list by fire count.

## 4. Full verification and release

- [x] 4.1 Run `uv run pytest -q` (full suite) and confirm clean.
- [x] 4.2 Manually run, from the worktree root:
      `CCST_FIRES_ACCESS=1 CCST_DATA_HOME=/tmp/ucc-smoke uv run python3
      src/cc_session_tools/skills/update-command-cache/scripts/update_command_cache.py list`
      and confirm it prints "No safe-verdict fires..." (empty synthetic data home) with no
      traceback, then repeat with plain `python3` (not `uv run`) to confirm that invocation now
      fails with a clear, expected `ModuleNotFoundError` rather than being silently assumed to work
      — this is the unsupported-invocation case the docstring now warns about, not a regression.
- [x] 4.3 Update `CHANGELOG.md` with a `### Fixed` entry describing the three bugs fixed, and bump
      `pyproject.toml`'s version as a patch release; run `uv lock` and commit the regenerated
      `uv.lock` in the same commit.
