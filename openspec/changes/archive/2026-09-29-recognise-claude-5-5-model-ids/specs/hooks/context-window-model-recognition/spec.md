## Purpose

Defines how the context-window warning identifies the running model's window size, display name
and standard input price from the model id recorded in the session transcript, so its
"percent of window" message is accurate for current models and conservative for unknown ones.

## ADDED Requirements

### Requirement: Current models are measured against their real context window
The context-window warning SHALL measure usage against a 1,000,000-token window and name the
model correctly for each of these model ids: `claude-sonnet-5-5` (Sonnet 5.5), `claude-opus-5-5`
(Opus 5.5) and `claude-fable-5-1` (Fable 5.1), in addition to the ids it already recognises.

#### Scenario: A Sonnet 5.5 session is reported against a 1M window
- **WHEN** a session whose last assistant message reports model `claude-sonnet-5-5` is at 266,000
  tokens when the warning fires
- **THEN** the message says about 27% of the 1M-token window and names Sonnet 5.5, and does not
  describe the model as unrecognized

#### Scenario: Opus 5.5 and Fable 5.1
- **WHEN** the model id is `claude-opus-5-5`, or `claude-fable-5-1`
- **THEN** the window is 1,000,000 tokens and the names are Opus 5.5 and Fable 5.1

### Requirement: A dated snapshot of a known model resolves to that model
A model id consisting of a known id followed by a hyphen and an eight-digit date SHALL resolve to
the same window, name and price as the known id.

#### Scenario: Dated Sonnet 5.5 snapshot
- **WHEN** the model id is `claude-sonnet-5-5-20261001`
- **THEN** it is treated as Sonnet 5.5: the same window, name and price as `claude-sonnet-5-5`

#### Scenario: A dated snapshot of an unknown model stays unknown
- **WHEN** the model id is `claude-sonnet-5-9-20261001`
- **THEN** it is treated as an unrecognized model

### Requirement: An unknown model id keeps the conservative default
A model id that is neither known nor a dated snapshot of a known id (including one that only
begins with a known id, or carries any other suffix) SHALL be treated as having a 200,000-token window and described as an
unrecognized model.

#### Scenario: A near-miss id is not matched by prefix
- **WHEN** the model id is `claude-sonnet-5-9`
- **THEN** the window is 200,000 tokens and the model is described as unrecognized
