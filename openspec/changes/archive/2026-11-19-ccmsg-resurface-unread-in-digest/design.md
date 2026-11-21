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

- Add a filtered query for `sent` description-addressed messages and, in `full` mode, merge them into the existing unclaimed-proposals section independent of the cursor (recipient-addressed messages are auto-read on delivery, so they are never pending). Alternative: stop advancing the cursor for proposals - rejected, it would re-nudge on every prompt.
- Cap at 5 lines plus a "+N more" tail. Alternative: unbounded - rejected, a backlog would flood session start.
- Age marker reuses the existing relative-age formatter used by the digest and `ccmsg list`.
- Retention keeps its single UPDATE but restricts to `status='claimed'` and `claimed_at`; `read_at` no longer makes a message eligible. Alternative: also exempt claimed messages - rejected, a claim is the explicit signal that someone took the message.
- Recipient-addressed messages are auto-read on first delivery, so they leave `sent` and are not re-listed; with retention no longer archiving them they stay visible in `ccmsg list` with age.

- A second filtered query returns `read` messages addressed to the session or its project; lines already shown as newly delivered in the same run are excluded. Separate section and separate cap from the unclaimed proposals, so neither crowds out the other. Alternative: only list `sent` - rejected, an auto-read message is read by a digest line, not handled.

## Risks / Trade-offs

- Stale unclaimed proposals add noise every session start → the cap bounds it and `ccmsg archive` clears one by hand.
- Read messages now stay listed until archived by hand, so a long backlog adds capped noise at every session start → `ccmsg archive <id>` clears one; a bulk-archive helper is out of scope.
