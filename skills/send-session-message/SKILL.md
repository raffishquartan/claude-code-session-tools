---
name: send-session-message
description: Use when you want to leave a durable message for another Claude Code session - a specific session, a whole project, or "whoever is working on X". Triggers on "tell the other session", "leave a note for the X project", "hand this off to whoever is doing Y", noticing something relevant to a different project, or coordinating two sessions without the user relaying by hand. Also use when a delivered description-addressed proposal arrives and this session may be the right place to claim it.
---

# Send a session message

`ccmsg` is the only sanctioned way to send a cross-session message. Compose with
care: the message is durable and auditable.

## When to send (proactively)

- You discover something relevant to a different project while working here.
- You are handing a sub-task to a session better placed to do it.
- Two sessions in the same project need to coordinate.

Do not send for things the user can see in this session, or to talk to yourself.

## Choose the recipient kind

Decide which of three addressing modes fits, and **confirm with the user when it
is ambiguous**:

1. `--to-session <uuid>` - a specific known session (you have its uuid).
2. `--to-project <name>` - any session in a named project.
3. `--to-description "<text>"` - "whoever is working on X". Surfaced to candidate
   sessions; one claims it.

If you are unsure which the user means, ask before sending.

## Compose

- Apply the user's writing-style rules: state the ask first, one point per
  message, cut filler.
- Attach by absolute path only (`--attach /abs/path`). The store references
  files; it does not copy them.

## Send

The delivery hook supplies your own session context. When you call `ccmsg send`,
pass the routing context the skill resolves from the current session (uuid,
project, partition). Subject and body are required; exactly one recipient kind
is required.

## Receiving a description-addressed proposal

When a delivered digest shows an unclaimed description-addressed message and this
session is the right place to handle it:

1. Propose to the user: summarise the message and ask whether to claim it.
2. On confirmation, run `ccmsg claim <id>`. First claim wins; if another session
   claimed it first you will be told, and you do nothing.

Read any message body with `ccmsg read <id>`.
