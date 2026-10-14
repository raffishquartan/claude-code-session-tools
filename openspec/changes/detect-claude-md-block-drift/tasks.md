## 1. Storage

- [ ] 1.1 Add `claude_md_blocks` and `claude_md_backups` tables to the sessions store schema (additive migration via the existing mechanism) and verify with a test that opens an old-schema database and finds both tables created
- [ ] 1.2 Add store helpers to record, read and delete a block hash and to add, list and fetch backups, each opened through the shared connection helper, and verify with unit tests including unknown-id fetch

## 2. Install behaviour

- [ ] 2.1 Record the block hash on apply and clear it on uninstall; verify dry runs write nothing
- [ ] 2.2 Before replacing a differing block, compare with the recorded hash and take a backup when it does not match or is missing; add the backup id to the result; verify the four scenarios (hand edit, stale template, no record, dry run) each with a test
- [ ] 2.3 Surface overwrites: print a warning per backed-up section in `ccst claude-md install` and the claude-md step of `install-everything`, and a stderr-only line in the auto-sync path; verify with tests that assert stdout is unchanged in the auto-sync case

## 3. Doctor and query subcommands

- [ ] 3.1 Add the `install:claude-md-drift` doctor check (WARN naming the section; OK otherwise) and verify with tests for both outcomes
- [ ] 3.2 Add `ccst claude-md backups list` and `show <id>` with `--help` text, and verify with CLI tests including the unknown-id error path

## 4. Release

- [ ] 4.1 Add a 3.16.0 entry to `CHANGELOG.md` (including the one-time extra backups note), bump `pyproject.toml` to 3.16.0, run `uv lock`, and verify the diff is limited to those files plus the feature
- [ ] 4.2 Run every configured check (pytest, mypy, `openspec validate --strict`) and verify all exit 0
