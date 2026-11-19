## 1. Retention

- [x] 1.1 Invert `test_read_15_days_old_is_archived` in `tests/messaging/test_retention.py` to assert a read message stays at any age, add a 60-day-old description-addressed `sent` case, keep the claimed-15-days case, and verify the new expectations fail before the fix
- [x] 1.2 Restrict `repository.archive_aged` to `status='claimed'` using `claimed_at`, update the `retention.py` docstring, and verify `CCST_NO_AUTO_SYNC=1 uv run pytest tests/messaging -q` passes

## 2. Session-start re-listing

- [x] 2.1 Add failing tests in `tests/messaging/` for: previously shown proposal re-listed in `full` mode, claimed/read message not listed, cap and "+N more" tail, empty digest on incremental delivery; verify they fail
- [x] 2.2 Add a repository query for `sent` messages in the swept partitions and a pending section in `service.deliver` for `full` mode using `targets()` and the shared age formatter; verify the new tests pass
- [x] 2.3 Verify re-listing across two successive full deliveries of a description-addressed message with `test_full_deliver_relists_previously_shown_unclaimed_proposal`

## 3. Read-but-unhandled listing

- [x] 3.1 Add tests for: read message listed at next session start and gone after `ccmsg archive`, no duplicate in the delivery that auto-reads it, project message read by another session listed, another session's message not listed, cap tail; verify with `CCST_NO_AUTO_SYNC=1 uv run pytest tests/messaging -q`
- [x] 3.2 Add the `unhandled_read` query and the separate digest section in `service.deliver`

## 4. Release

- [x] 4.1 Add a CHANGELOG entry, bump `pyproject.toml` to 4.2.0, run `uv lock`, and verify the full test suite exits 0
