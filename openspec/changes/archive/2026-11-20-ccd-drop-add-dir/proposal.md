## Why

Since Claude Code 2.1.326, every `--add-dir` folder that has no trusted entry in `~/.claude.json` triggers a blocking
"Adding a new directory" prompt at startup, and the answer is never saved (upstream
anthropics/claude-code issue 110642, open). `ccd` passes `--add-dir` for every project under each session root, so
a launch can stop on one prompt per untrusted project, on every start.

The flags were added so a session could reach sibling projects without listing each one in `settings.json`'s
`permissions.additionalDirectories` (which has no globs). That need is already met by a single ancestor entry such
as the home directory, which both session roots sit under. A headless session started without `--add-dir` read files
in a sibling repo and in a session-root project with no permission denial.

## What Changes

- `ccd` stops passing `--add-dir` arguments. `trusted_subdirectories()` and its call site are deleted.
- `ccst doctor` gains a check that WARNs when a configured session root is not equal to or under any entry in
  `permissions.additionalDirectories` of `~/.claude/settings.json`, and says which entry to add. This keeps the
  access the flags used to grant explicit and checkable on every machine instead of silently assumed.
- The `cli/ccd-trusted-dirs` requirement that `ccd` passes `--add-dir` for every project is removed.
- Patch release: this is a bug fix with no interface change a user types.

## Capabilities

### New Capabilities
- `cli/doctor-session-root-access`: `ccst doctor` verifying that every configured session root is covered by an
  `additionalDirectories` entry.

### Modified Capabilities
- `cli/ccd-trusted-dirs`: `ccd` no longer passes `--add-dir`; the requirement that it did is removed and replaced by
  one stating it passes none.

## Impact

- `src/cc_session_tools/cli/ccd.py`, `src/cc_session_tools/lib/roots.py` (delete `trusted_subdirectories`)
- `src/cc_session_tools/lib/doctor.py` (new check, wired into `run_all_checks`)
- Tests: `tests/test_roots.py`, `tests/test_cli_ccd.py`, `tests/test_ccst_doctor.py`; the `--add-dir` mentions in
  `tests/scheduler/test_ccsched_cli.py` and `tests/pdata/test_verify.py` are checked and updated if they depend on it
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (4.1.2 to 4.1.3)
- Machines whose `additionalDirectories` does not cover the session roots will see the new doctor WARN. Their
  sessions were already prompting for every sibling project, so nothing gets worse.
