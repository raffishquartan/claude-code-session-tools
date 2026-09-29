# hooks/context-window-warning-token-count Specification

## Purpose

Defines which transcript entry the context-window warning measures, so the token count and model
it reports reflect the session's last real API response.

## Requirements

### Requirement: The warning measures the last real assistant response
The context-window warning SHALL take its token count and model id from the last non-sidechain
transcript entry that carries a usage block and whose model is not `<synthetic>`. Entries with
model `<synthetic>` SHALL be ignored regardless of their usage values.

#### Scenario: A synthetic stub after a large real response
- **WHEN** the last real assistant entry reports 180,000 context tokens on `claude-sonnet-5-5` and
  a later `<synthetic>` entry reports zero usage
- **THEN** the warning measures 180,000 tokens, fires, and names Sonnet 5.5

#### Scenario: Only synthetic entries
- **WHEN** every assistant entry with a usage block is `<synthetic>`
- **THEN** the warning treats the session as fresh (zero tokens, no model) and stays silent
