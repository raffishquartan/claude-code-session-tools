## 1. Command

- [ ] 1.1 Add failing tests in `tests/messaging/` for each scenario (read restored, never-read restored, claimed refused, not-archived refused, unknown id with others still restored and non-zero exit); verify they fail
- [ ] 1.2 Add `repository.unarchive_one` and `service.unarchive` with the claim lock, and the `ccmsg unarchive` subcommand; verify `CCST_NO_AUTO_SYNC=1 uv run pytest tests/messaging -q` passes

## 2. Release

- [ ] 2.1 Add a CHANGELOG entry, bump `pyproject.toml` to 4.3.0, run `uv lock`, and verify the full test suite exits 0
