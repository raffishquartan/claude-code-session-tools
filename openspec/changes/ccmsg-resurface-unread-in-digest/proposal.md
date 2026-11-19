## Why

Hand-off messages go unnoticed (request: message 20261119T175849Z-bacf, refined by the user). Two behaviours cause it. First, retention archives `read` messages 14 days after they were read, even though a message that was auto-read by a session digest has never been claimed or acted on. Second, a description-addressed proposal is shown to a session once and then never again, so an unclaimed proposal silently drops out of view. Messages still in status `sent` are already never archived.

## What Changes

- **BREAKING (behaviour)**: automatic retention archives only `claimed` messages older than the retention window. `sent` and `read` messages are never auto-archived at any age; they leave the live set only via `ccmsg claim` then retention, or an explicit `ccmsg archive`.
- At session start (`deliver` in `full` mode) the digest also re-lists every still-`sent` message that targets the session, with an age marker, regardless of the cursor. Capped at a fixed number of lines with a "+N more, see `ccmsg list`" tail.
- Per-prompt (incremental) delivery is unchanged.
- Minor version bump and CHANGELOG entry.

## Capabilities

### New Capabilities
- `cli/ccmsg-unread-resurface`: which messages automatic retention may archive, and what the session-start digest re-lists with age.

### Modified Capabilities

## Impact

- `src/cc_session_tools/lib/messaging/repository.py` (`archive_aged`), `retention.py`, `service.py` (`deliver`, digest formatting), tests under `tests/messaging/` (the existing "read 15 days old is archived" test inverts), `CHANGELOG.md`, `pyproject.toml`, `uv.lock`.
- No schema or on-disk format change: no migration and no `ccst doctor` check.
- Read messages now accumulate until archived by hand; `ccmsg list` shows their age.
