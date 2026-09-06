## Purpose

Ensures the 8-digit confirmation gate's block/warn outcome reaches the user even when they are
away from the terminal, by pushing a Telegram notification alongside its existing stderr output.

## ADDED Requirements

### Requirement: A blocked or warned gated call sends a Telegram notification
When the 8-digit confirmation gate decides to block or warn a gated tool call, the system SHALL
attempt to send a Telegram notification describing the outcome, in addition to its existing
stderr message.

#### Scenario: A blocked call notifies the user
- **WHEN** the gate blocks a gated tool call (verification failed and enforcement is set to
  block)
- **THEN** a Telegram notification describing the block is sent, in addition to the stderr
  message already printed

#### Scenario: A warned call notifies the user
- **WHEN** the gate would have blocked a gated tool call but enforcement is set to warn
- **THEN** a Telegram notification describing the warning is sent, in addition to the stderr
  message already printed

#### Scenario: An allowed call sends no notification
- **WHEN** the gate allows a gated tool call (verified, or an exception applies)
- **THEN** no Telegram notification is sent

### Requirement: A failed notification never changes the gate's decision
The gate's block/allow/warn decision and exit code SHALL be unaffected by whether the Telegram
notification succeeds or fails.

#### Scenario: Telegram is unreachable or unconfigured
- **WHEN** the Telegram notification cannot be sent (missing credentials, network failure, or any
  other transport error)
- **THEN** the gate's exit code and stderr message are exactly what they would have been without
  attempting the notification
