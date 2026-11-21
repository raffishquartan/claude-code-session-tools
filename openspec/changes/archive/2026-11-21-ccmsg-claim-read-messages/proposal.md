## Why

A message addressed to a session or project is marked `read` the moment a digest first shows it, so it has no claim step, and the only way it leaves the "read but not yet handled" list is `ccmsg archive`. That overloads archive with two meanings ("dealt with" and "tidied away") and gives description-addressed and session/project messages different lifecycles. Claim already means "a session took responsibility for this"; using it for every address kind gives one lifecycle.

## What Changes

- `ccmsg claim` accepts a message in status `read` (auto-read session or project messages) as well as `sent`. First claim wins, so a project message claimed by one session stops listing for every session of the project.
- A claim on an already-read message keeps the original reader stamp, so the sender's receipt is unchanged.
- The session-start digest's "Read but not yet handled" section tells the session to run `ccmsg claim <id>` once the message is dealt with, and prints the full command including the session's own `--uuid` and `--session` values, so it can be run as shown.
- `ccmsg archive` on an unclaimed message stays allowed as an explicit "discard without handling"; it does not set a claim.
- Lifecycle for every address kind: `sent` -> `read` (shown) -> `claimed` (dealt with) -> `archived`. Retention is unchanged (claimed only).
- Bundled guidance (global CLAUDE.md snippet and the `send-session-message` skill) says a session or project message is claimed once handled, not only description-addressed ones.
- No on-disk change: existing `read` rows are simply unclaimed until someone claims them. Minor version bump.

## Capabilities

### New Capabilities
- `cli/ccmsg-claim-handled`: claim marks any message handled, whatever its address kind, and an auto-read message can be claimed.

### Modified Capabilities
- `cli/ccmsg-unread-resurface`: the read-but-unhandled list is cleared by claim, not by archive.

## Impact

- `src/cc_session_tools/lib/messaging/repository.py` (`claim`, `unhandled_read` docstring), `service.py` (digest hint), `cli/ccmsg.py` (claim help text), `lib/claude_md_install.py` and `skills/send-session-message/SKILL.md` (guidance).
- Tests under `tests/messaging/`. `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (minor bump, number set at apply time after the stack is rebased on the current `main`).
- Stacked on the `ccmsg-unarchive` branch (PR 186).
