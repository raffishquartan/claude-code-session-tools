## Context

Status model today: `sent` -> `read` (auto, session/project) or `claimed` (description) -> `archived`. `repository.claim` rejects `read`, because description messages never reach `read` and a claim on a `read` message was meant to mean "someone already took it". PR 185 added a read-but-unhandled digest section cleared only by `ccmsg archive`.

## Decisions

- **Claim accepts `sent` and `read`.** One predicate change in `repository.claim` (`status IN ('sent','read')`), still inside `BEGIN IMMEDIATE`, so first-claim-wins is unchanged. Alternative rejected: a new status such as `handled`; it adds a state, a migration-free but wider change, and duplicates what `claimed` already means.
- **Keep the original reader.** On a `read` row the UPDATE keeps `read_at`, `read_by_uuid`, `read_by_session` (already COALESCEd for time; the uuid/session fields get the same treatment) and sets only `claimed_at`, status and version. The claimer is therefore not recorded for project/session messages; accepted, since no consumer needs it and receipts stay correct.
- **Project messages: first claim wins.** A project message claimed in one session stops listing in all sessions of the project. This matches the description-message semantics and the user's decision.
- **Archive stays as "discard".** Not requiring a claim keeps `ccmsg archive` simple and keeps `ccmsg unarchive` meaningful (it only restores never-claimed messages).
- **Retention unchanged.** It already archives only `claimed` messages, so claiming a read message now makes it eligible 14 days after the claim.
- **Runnable hint.** `ccmsg claim` requires `--uuid` and `--session`; the digest knows both (`ctx.uuid`, `ctx.project`), so it prints them. Making the flags optional was considered and rejected here: a CLI invocation outside a hook has no reliable way to know the session uuid.

## Risks

- A session may claim a message it only skimmed. Mitigated by wording: claim only after the message is dealt with, as already required for description messages.
- Existing `read` messages (including any restored by `ccmsg unarchive`) list at every session start until claimed. This is the intended behaviour.

## Migration

None. No schema or data change; existing `read` rows are claimable as-is.
