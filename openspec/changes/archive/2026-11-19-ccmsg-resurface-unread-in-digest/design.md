## Context

`service.deliver` sweeps partitions from a per-session cursor, auto-reads recipient messages, shows each description proposal once and advances the cursor; `retention.archive_old` runs per partition afterwards. `deliver` already takes `mode` (`full` vs `incremental`) but treats both identically. See proposal.md for the discrepancy with the request.

## Goals / Non-Goals

**Goals:**
- Pending (`sent`) messages stay visible at every session start, with age.
- The never-auto-archive-unread rule is specified and tested.

**Non-Goals:**
- Changing the 14-day window for claimed messages.
- Re-listing on every prompt.
- Changing auto-read semantics or the receipt flow.

## Decisions

- Add a query for `sent` messages targeting the session (reuse `targets()`), and in `full` mode append them as a "pending" section independent of the cursor. Alternative: stop advancing the cursor for proposals - rejected, it would re-nudge on every prompt.
- Cap at 5 lines plus a "+N more" tail. Alternative: unbounded - rejected, a backlog would flood session start.
- Age marker reuses the existing relative-age formatter used by the digest and `ccmsg list`.
- Retention keeps its single UPDATE but restricts to `status='claimed'` and `claimed_at`; `read_at` no longer makes a message eligible. Alternative: also exempt claimed messages - rejected, a claim is the explicit signal that someone took the message.
- Recipient-addressed messages are auto-read on first delivery, so they leave `sent` and are not re-listed; with retention no longer archiving them they stay visible in `ccmsg list` with age.

## Risks / Trade-offs

- Stale unclaimed proposals add noise every session start → the cap bounds it and `ccmsg archive` clears one by hand.
- Read messages accumulate until archived by hand → `ccmsg list` shows age so they can be cleared; a bulk-archive helper is out of scope.
