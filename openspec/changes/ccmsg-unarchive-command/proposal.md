## Why

Messages that were never claimed or acted on have been auto-archived by earlier versions of retention, and there is no way to bring one back: `ccmsg archive` is one-way. Restoring them is needed so they show up again at session start and can be handled.

## What Changes

- New `ccmsg unarchive <id> [<id> ...]` command that restores archived messages that were never claimed.
- A restored message returns to `read` if it had been read, otherwise `sent`, so the session-start digest lists it again (see `ccmsg-unread-resurface`).
- A message that was claimed is refused (someone took it), as is one that is not archived or does not exist. Each id is reported; the exit status is non-zero if any failed.
- Minor version bump and CHANGELOG entry.

## Capabilities

### New Capabilities
- `cli/ccmsg-unarchive`: restoring archived, never-claimed messages with `ccmsg unarchive`.

### Modified Capabilities

## Impact

- `src/cc_session_tools/cli/ccmsg.py`, `lib/messaging/service.py`, `lib/messaging/repository.py`, tests under `tests/messaging/`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock`.
- No schema or on-disk format change: no migration and no `ccst doctor` check.
- Restored `read` messages are only safe from re-archiving once the retention change (claimed-only archiving) is installed; older installs archive `read` messages 14 days after they were read.
