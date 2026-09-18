## Why

Running the `update-command-cache` skill on 2026-09-12 surfaced three real defects in
`src/cc_session_tools/skills/update-command-cache/` (script + `SKILL.md`), reported via `ccmsg`
`20260912T122716Z-c24b`:

- The script's `sys.path` bootstrap computes the wrong directory (`Path(__file__).parents[3] /
  "src"`, which resolves to a nonexistent `.../cc_session_tools/src`) trying to make `hooks` and
  `cc_session_tools.lib` importable. This reproduces with any interpreter, including inside a repo
  checkout: `/usr/bin/python3 src/cc_session_tools/skills/update-command-cache/scripts/update_command_cache.py
  list` fails with `ModuleNotFoundError: No module named 'hooks'`. The bootstrap is also
  unnecessary — both `hooks` and `cc_session_tools.lib` are real top-level packages (installed the
  same way `hooks.stats` already is via the `cccs-stats` console-script entry point), so any
  interpreter that actually has `cc-session-tools` installed (`uv run` in a checkout, or the venv
  `uv tool install` creates) imports them directly with no path manipulation at all.
- `SKILL.md` documents a flag-based CLI (`--list` / `--remove <sha>` / `--flip <sha> <verdict>`)
  that the script does not implement — it takes subcommands (`list` / `remove` / `flip` /
  `promote`) instead, and rejects the documented flag forms outright. The script's own module
  docstring has the same stale flag-based `Usage:` block.
- `SKILL.md`'s workflow step 4 promises the tool "presents the candidate list to the user with
  command preview" — but `telemetry_events` has no plaintext-command column, only a `input_hash`
  (SHA-256); `cmd_list`'s own output already says as much. `SKILL.md` also documents the cache's
  path as `~/.cache/claude/logs/command-cache.db`; the real path (`hooks/cache.py`'s own docstring)
  is `~/.local/share/claude/command-cache.db` (overridable via `CCST_CACHE_DB`).

## What Changes

- Remove the broken, unnecessary `sys.path` bootstrap from `update_command_cache.py`; import
  `hooks.cache` and `cc_session_tools.lib.telemetry_store` directly, matching how the sibling
  `move-session` skill's `cc_session_rules.py` already imports `cc_session_tools.lib.roots` with no
  bootstrap. Fix the module docstring's `Usage:` block to show the actual subcommands and to say
  explicitly that the script must be run through an interpreter that has `cc-session-tools`
  installed (`uv run python3 <path>` in a checkout; the tool's own venv interpreter — the same one
  `ccst` runs under — once installed) rather than a bare system `python3`.
- Update `SKILL.md`: replace the stale `--list`/`--remove`/`--flip` flag documentation with the
  real `list`/`remove`/`flip`/`promote` subcommands; correct the cache DB path; replace the
  undeliverable "command preview" promise in the workflow section with the actual, already-shipped
  behavior (`cmd_list` prints sha/fire-count/last-seen and tells the caller to recover the plaintext
  command by hand from session transcripts) documented as the real, manual workaround rather than an
  automated promise.
- Note in `SKILL.md` that `telemetry.db`'s `bash-security-review` rows can include synthetic test
  fixture rows (`session_id` values like `test-session-*`) alongside production fires, so a
  curation pass isn't surprised by a synthetic session dominating the candidate list; no code change
  — deciding whether test fixtures should write to a separate database is out of scope for this fix.

## Capabilities

### New Capabilities
- `skills/update-command-cache`: the script's supported invocation (no bootstrap, must run under an
  interpreter with `cc-session-tools` installed) and `SKILL.md`'s documented CLI surface, cache
  path, and command-preview behavior. Not previously specced.

### Modified Capabilities
(none)

## Impact

- `src/cc_session_tools/skills/update-command-cache/scripts/update_command_cache.py`: drop the
  `sys.path` bootstrap block; fix the module docstring.
- `src/cc_session_tools/skills/update-command-cache/SKILL.md`: CLI docs, cache path, workflow step
  4, synthetic-rows note.
- `src/cc_session_tools/skills/update-command-cache/tests/test_update_command_cache.py`: new
  regression test invoking the script as a real subprocess (the shape the reported bug actually
  manifested in) to catch a reintroduced broken bootstrap.
- Patch version bump (`pyproject.toml`, `CHANGELOG.md`) — bug fix and doc correction, no CLI
  interface change (the subcommands were already `list`/`remove`/`flip`/`promote`; only the docs
  were wrong).
