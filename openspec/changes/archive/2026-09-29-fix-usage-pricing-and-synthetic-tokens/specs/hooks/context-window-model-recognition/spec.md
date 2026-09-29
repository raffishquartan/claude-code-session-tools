## ADDED Requirements

### Requirement: The cost estimate uses each model's published cache-read price
The context-window warning's per-turn cache-read cost estimate SHALL multiply the context tokens by
the model's published cache-read price (not a fixed fraction of its input price), and SHALL use
$0.50 per million tokens for an unrecognized model.

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
