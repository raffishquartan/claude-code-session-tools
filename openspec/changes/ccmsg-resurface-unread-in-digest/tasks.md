## 1. Retention

- [x] 1.1 Invert `test_read_15_days_old_is_archived` in `tests/messaging/test_retention.py` to assert a read message stays at any age, add a 60-day-old description-addressed `sent` case, keep the claimed-15-days case, and verify the new expectations fail before the fix
- [x] 1.2 Restrict `repository.archive_aged` to `status='claimed'` using `claimed_at`, update the `retention.py` docstring, and verify `CCST_NO_AUTO_SYNC=1 uv run pytest tests/messaging -q` passes

## 2. Session-start re-listing

- [x] 2.1 Add failing tests in `tests/messaging/` for: previously shown proposal re-listed in `full` mode, claimed/read message not listed, cap and "+N more" tail, empty digest on incremental delivery; verify they fail
- [x] 2.2 Add a repository query for `sent` messages in the swept partitions and a pending section in `service.deliver` for `full` mode using `targets()` and the shared age formatter; verify the new tests pass
- [ ] 2.3 Verify end to end by sending a description-addressed message with `ccmsg send` and running `ccmsg deliver` full twice under different session contexts, confirming both list it with an age

## 3. Release

- [x] 3.1 Add a CHANGELOG entry, bump `pyproject.toml` to 4.2.0, run `uv lock`, and verify the full test suite exits 0
