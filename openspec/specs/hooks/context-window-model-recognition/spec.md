# hooks/context-window-model-recognition Specification

## Purpose

Defines how the context-window warning identifies the running model's window size, display name
and cache-read price from the model id recorded in the session transcript, so its
"percent of window" message is accurate for current models and conservative for unknown ones.

## Requirements

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
- **THEN** `is_known` is false and the window is 200,000 tokens

### Requirement: An unknown model id keeps the conservative default
A model id that is neither known nor a dated snapshot of a known id (including one that only
begins with a known id, carries any other suffix, or is empty) is unknown. `model_info.is_known`
SHALL return false for it and its window SHALL be 200,000 tokens. That window is an assumption, and
the context-window warning SHALL present it as one.

#### Scenario: A near-miss id is not matched by prefix
- **WHEN** the model id is `claude-sonnet-5-9`
- **THEN** the window is 200,000 tokens and `is_known` is false

#### Scenario: An empty id
- **WHEN** the model id is the empty string
- **THEN** the window is 200,000 tokens and `is_known` is false

#### Scenario: Known ids are known
- **WHEN** the model id is `claude-sonnet-5-5`, or a dated snapshot such as `claude-sonnet-5-5-20261001`
- **THEN** `is_known` is true

### Requirement: The cost estimate uses each model's published cache-read price
The context-window warning's per-turn cache-read cost estimate SHALL multiply the context tokens by
the model's published cache-read price (not a fixed fraction of its input price), and SHALL use
$0.50 per million tokens for an unknown model, saying so in the message.

#### Scenario: Fable 5.1's lower cache-read rate
- **WHEN** the warning fires at 400,000 tokens on `claude-fable-5-1` (cache reads $0.25 per million)
- **THEN** the estimate is $0.10 per turn

#### Scenario: Sonnet 5.5
- **WHEN** the warning fires at 500,000 tokens on `claude-sonnet-5-5` (cache reads $0.20 per
  million)
- **THEN** the estimate is $0.10 per turn

### Requirement: Mythos 5.1 is measured against its real context window
The context-window warning SHALL measure `claude-mythos-5-1` against a 1,000,000-token window and
name it Mythos 5.1.

#### Scenario: A Mythos 5.1 session
- **WHEN** the model id is `claude-mythos-5-1`
- **THEN** the window is 1,000,000 tokens and the name is Mythos 5.1

### Requirement: The warning says plainly when the window is assumed
When the model id is unknown, the context-window warning SHALL, in both its instruction text and its
visible nudge line: contain the word `ASSUMED` and the phrase `may be much larger`; quote the model id
the hook saw in backticks (or say `no model id was recorded` when it is empty), with every character
outside `A-Z a-z 0-9 . _ : < > [ ] -` replaced by `?`; mark the percentage `unreliable`; and say the
dollar estimate `assumes the default $0.50/MTok cache-read price` and the window `200k`, both
derived from the same values the hook uses. The visible nudge line SHALL also name
`src/hooks/model_info.py` as the file to update and give `uv tool upgrade cc-session-tools`
(installed from PyPI with uv) and `uv tool install --reinstall <path to your checkout>` (local
checkout) as the ways to refresh the installed ccst, without any personal path. Neither text SHALL
describe the window as that of "an unrecognized model". The message for a known model SHALL be
unchanged.

#### Scenario: An unknown id
- **WHEN** the warning fires at 162,000 tokens on model `claude-sonnet-9-9`
- **THEN** both texts contain `` `claude-sonnet-9-9` ``, `ASSUMED`, `may be much larger`,
  `unreliable` and `assumes the default $0.50/MTok cache-read price`; the estimate is ~$0.08/turn;
  the visible line contains `src/hooks/model_info.py`, `uv tool upgrade cc-session-tools` and
  `uv tool install --reinstall`; neither text contains "unrecognized model" or "unrecognised model"

#### Scenario: A dated snapshot of an unknown id
- **WHEN** the model id is `claude-sonnet-9-9-20261001`
- **THEN** the message quotes that exact string and uses the assumed-window wording

#### Scenario: An empty model id
- **WHEN** a real (non-synthetic) assistant entry has usage but an empty or missing model
- **THEN** the message uses the assumed-window wording and says `no model id was recorded`

#### Scenario: A hostile model id
- **WHEN** the model id contains a single quote, a backtick or a newline
- **THEN** those characters appear as `?` in the message, so the quoted lines stay intact

#### Scenario: A known model is unchanged
- **WHEN** the warning fires on `claude-sonnet-5-5`
- **THEN** the message equals the existing concise known-model form exactly and contains no `ASSUMED`

#### Scenario: Remediation text has no personal data
- **WHEN** the remediation hint is inspected
- **THEN** it contains no `/Users/`, `/home/` or user name
