## Why

`move_session.py --verify-only` re-checks a destination transcript against its source after a move. After a MOVE with the tombstone enabled (the default) the source carries two more lines than the destination, because the tombstone is appended to the source after the copy, so verify-only prints `RESULT : FAIL` on line count. That FAIL is expected, but nothing in the output says so, and the SKILL.md instruction "do not present the move as successful" reads as if the move had gone wrong.

## What Changes

- When `--verify-only` finds the source longer than the destination, the verification output explains why: it names the number of extra source lines and, when they are tombstone records, states that the FAIL on line count is expected after a move with the tombstone enabled and that the remaining rows are the ones to check.
- When the extra source lines are not explained by tombstone records, the output says so instead, so a real divergence is not dismissed.
- `SKILL.md` documents the expected FAIL and how to read it.
- The result value and exit code are unchanged.

## Capabilities

### New Capabilities

### Modified Capabilities
- `skills/move-session`: adds a requirement that verification output explains a line-count FAIL caused by source tombstone records.

## Impact

- `src/cc_session_tools/skills/move-session/scripts/move_session.py` (verification result and printing), its tests, and `SKILL.md`.
- `openspec/specs/skills/move-session/spec.md` (new requirement after sync).
- Released with the combined 4.1.0 release; its CHANGELOG entry is written at the release step.
