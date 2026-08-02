---
name: select-agent-model
description: Use this skill before every call to the Agent tool to decide which model tier to dispatch to. ALWAYS check it when about to spawn a subagent, whether foreground or background - triggers on "dispatch an agent", "spawn a subagent", "use the Agent tool", or any point where a task is about to be delegated rather than done inline.
---

# Select agent model

**Use this before calling the Agent tool.** Pick the model tier for the
subagent being dispatched, then state that choice in the first line of the
agent's prompt so it is visible in the audit trail.

## The `model` param selects by family, not by version

`model` on the Agent tool takes `sonnet` / `opus` / `haiku` / `fable` — a
**family**, not a pinned version. It always resolves to the current release
in that family. "Use the newer version automatically when one ships" is
already the tool's default behaviour - there is nothing to update when a new
model ships within a family.

## Decision

- **Default: Sonnet-tier** (`model: "sonnet"`). Use for anything clear,
  well-scoped, and unlikely to require subtle judgment. This covers the large
  majority of dispatched tasks.
- **Opus-tier** (`model: "opus"`) only when:
  - The task involves substantial design, ambiguity, or cross-cutting
    reasoning where a smaller model would produce a meaningfully worse
    outcome.
  - The task involves writing or refactoring complex code in a tricky
    domain.
- The model of the dispatching session is independent of the model chosen
  for the agent - a Sonnet-tier session can dispatch an Opus-tier agent and
  vice versa.

## Audit trail

State the model choice in the agent prompt's first line, e.g.:

```
Model: sonnet. Review the migration file for safety issues.
```

This makes the choice visible in `prompt.md` (or the equivalent transcript)
without needing to inspect the tool-call parameters separately.

## Relationship to the O-E-A-C loop

If the task at hand is iterative/incremental or complicated/subtle enough
that a single agent dispatch isn't sufficient quality assurance, this skill
only picks the model for one dispatch - see the
`do-executor-critic-assessor-loop` skill for whether and how to structure
multiple rounds of dispatch.
