# notify/confirm-8digit-gated-tools-config Specification

## Purpose

Defines where the 8-digit confirmation gate's gated-tool allowlist comes from, so this
personal-policy configuration (which MCP tools count as sensitive enough to require the 8-digit
gate) lives outside CCST's hardcoded, publicly-published source.

## Requirements

### Requirement: The gated-tool list comes from an environment variable, not a hardcoded default
The 8-digit confirmation gate SHALL read its gated-tool allowlist from the
`CCCS_CONFIRM_8DIGIT_GATED_TOOLS` environment variable (a comma-separated list of tool names,
whitespace around each name trimmed, empty entries dropped) rather than from a value hardcoded in
CCST's source. When the variable is unset or empty, the gate treats no tool as gated.

#### Scenario: Variable unset
- **WHEN** `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` is unset
- **THEN** the gate allows every tool call unconditionally (no tool is treated as gated)

#### Scenario: Variable set with multiple tool names
- **WHEN** `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` is set to `"tool-a, tool-b,tool-c"`
- **THEN** the gate treats exactly `tool-a`, `tool-b`, and `tool-c` as gated, with surrounding
  whitespace stripped from each name

#### Scenario: Variable set with an empty entry
- **WHEN** `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` is set to `"tool-a,,tool-b"`
- **THEN** the gate treats `tool-a` and `tool-b` as gated and does not treat the empty string as a
  gated tool name

### Requirement: CCST ships no default gated tools
CCST's source SHALL NOT hardcode any specific tool name as gated by default - the concrete list
of which tools require 8-digit confirmation is a deployment's own personal-policy configuration,
supplied externally via `CCCS_CONFIRM_8DIGIT_GATED_TOOLS`.

#### Scenario: A fresh install gates nothing until configured
- **WHEN** CCST is freshly installed and `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` has never been set
- **THEN** the 8-digit confirmation gate allows every tool call unconditionally, until the
  deploying user sets the environment variable themselves
