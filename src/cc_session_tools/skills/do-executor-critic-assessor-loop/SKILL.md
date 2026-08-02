---
name: do-executor-critic-assessor-loop
description: Use when a task is non-trivial, creative, or subtle enough that a single agent dispatch risks a meaningfully worse result than iterating would - "make this really good", "iterate until this is right", "I want this reviewed and refined", complex documents, designs, or code where quality matters more than speed. Runs a four-role orchestrator/executor/critic/assessor loop across sequential Agent() calls. Do NOT use for simple, clear, well-scoped tasks - dispatch a single agent instead.
---

# Orchestrator-executor-critic-assessor loop

For non-trivial creative or technical work where quality matters more than
speed, use a four-role agent pattern that iterates a candidate through
critique and revision until it converges.

## Decision gate - check this before running the loop

Three options exist for improving a candidate through iteration. Pick one:

1. **Single-shot dispatch.** The task is clear enough that one agent call
   (see `select-agent-model`) will do. Most tasks land here - don't reach
   for a loop by default.
2. **`Workflow` tool's judge-panel / iterate-until-converged pattern.**
   When the solution space is wide and several genuinely different
   *attempts* (not just refinements) are worth generating in parallel and
   scored against each other. This requires the user's **explicit opt-in**
   (the "ultracode" keyword, ultracode mode being on, or an explicit ask to
   use a workflow/multi-agent orchestration) - it cannot be launched
   silently. If the task fits this shape but the user hasn't opted in,
   describe the option and ask rather than launching it.
3. **This skill's manual O-E-A-C loop.** The task is a single evolving
   candidate (a document, a design, a piece of code) that benefits from
   structured critique-and-revise rounds, run as sequential `Agent()` calls
   without needing `Workflow`-level orchestration or the user's opt-in.
   Use this when the loop is a handful of rounds against one candidate,
   not a fan-out across many independent attempts.

The rest of this skill describes option 3.

## The four roles

1. **Orchestrator** (usually the main session, or a dedicated orchestrator
   agent): defines the task, writes a **quality rubric** describing what a
   good final output looks like, and runs the loop below. The rubric
   becomes part of every executor / critic / assessor prompt.

2. **Executor** (Sonnet-tier default - see `select-agent-model`): produces
   or improves a candidate output. On iteration 1, works from the initial
   prompt; on later iterations, works from the previous output plus the
   assessor's prioritised feedback.

3. **Critic** (Sonnet-tier, or Opus-tier if the work is subtle): challenges
   and verifies the current output. Checks accuracy, completeness, internal
   consistency, and alignment with the rubric. Produces a structured
   critique. The critic should NOT also propose the fix - its job is to
   find problems, not solve them.

4. **Assessor** (separate from critic; Opus-tier by default): reads the
   critique and the current output, decides which criticisms are valid,
   produces a prioritised list of changes the next executor pass should
   make, scores the output against each rubric item, and returns one of
   three verdicts: `converged`, `iterate`, or `stop`.

## Loop termination

The orchestrator loops `executor -> critic -> assessor` until:

- The assessor returns `converged`; OR
- A pre-declared iteration cap is hit (default 5); OR
- The orchestrator decides it is pragmatic to stop for other reasons
  (cost, time, diminishing returns). The reason MUST be documented in the
  orchestrator's `WORKLOG.md`.

## Agent folder layout

Each role gets its own agent folder per iteration:

```
agents/<session-tag>--executor-<n>/
agents/<session-tag>--critic-<n>/
agents/<session-tag>--assessor-<n>/
```

The orchestrator's own folder (if dispatched as an agent rather than being
the main session) is `agents/<session-tag>--orchestrator/`. See the
`agent-usage.md` conventions for what each agent folder must contain
(`prompt.md`, `WORKLOG.md`, deliverables).

## Cost note

This pattern is expensive - three-plus agent dispatches per iteration, up to
the iteration cap. Use it only when warranted. Simple work should stay as a
single executor dispatched via `select-agent-model`.
