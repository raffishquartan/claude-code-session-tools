## Context

`ccmsg archive` flips status to `archived` under the claim lock; `repository.archive_one` does the update. Messages keep `read_at` and `claimed_at` when archived, so the previous state is recoverable. See proposal.md.

## Goals / Non-Goals

**Goals:**
- A safe, scriptable restore for several ids at once.

**Non-Goals:**
- Restoring claimed messages, bulk selection flags, or changing archive semantics.

## Decisions

- Derive the restored status from stored timestamps (`read_at` set gives `read`, else `sent`) rather than adding a column. Alternative: store the pre-archive status - rejected, it needs a schema change for no extra information.
- Refuse claimed messages. Restoring one to `claimed` would be re-archived by retention at once, and `read` would misrepresent a taken message.
- Take the claim lock and use one `BEGIN IMMEDIATE` transaction per id, as `archive` does.

## Risks / Trade-offs

- Restored `read` messages are re-archived after 14 days by installs that predate claimed-only retention → install the retention release before restoring in bulk.
