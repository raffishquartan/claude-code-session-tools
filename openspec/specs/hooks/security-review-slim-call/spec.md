# hooks/security-review-slim-call Specification

## Purpose
Bound the context cost of the security-review hook's LLM escalation so a one-line command review
does not load a full interactive session's worth of context.

## Requirements

### Requirement: Review child loads minimal context
The hook's `claude -p` escalation SHALL NOT load slash commands, MCP servers, or project-level
instructions, and SHALL be measured against a documented acceptance bar before adoption.

#### Scenario: measured reduction
- **WHEN** the adopted invocation is run over the reference command corpus
- **THEN** first-request cache-creation tokens are at least 70% lower than the previous invocation's

#### Scenario: verdict preserved
- **WHEN** each reference-corpus command is reviewed with the adopted invocation
- **THEN** the output contains SUMMARY, RISKS and VERDICT lines and each VERDICT equals the verdict from the previous invocation or is strictly more severe

### Requirement: Review child still authenticates on a subscription
The adopted invocation SHALL NOT require `ANTHROPIC_API_KEY`.

#### Scenario: OAuth-only environment
- **WHEN** the hook runs with no `ANTHROPIC_API_KEY` set
- **THEN** the review succeeds using the user's existing login

### Requirement: Failure remains non-blocking
An unsupported flag or child failure SHALL yield an "unavailable" review message and allow the
command, never block it and never silently retry with a heavier invocation.

#### Scenario: child exits non-zero
- **WHEN** the review child exits non-zero
- **THEN** stderr shows `[security review unavailable: ...]`, the hook exits 0, and telemetry records verdict `unavailable`
