## ADDED Requirements

### Requirement: Send guidance recommends a body file for long or code-like bodies
`ccmsg send --help` and the bundled `send-session-message` skill SHALL recommend `--body-file` over inline `--body` for a long or code-like body, and SHALL give the reason: PreToolUse Bash hooks match the full command text, so an inline body that only mentions a blocked command can block the send. The skill SHALL suggest writing the body file under the session's `working/` folder. No hook behaviour changes.

#### Scenario: Send help recommends body file
- **WHEN** `ccmsg send --help` is run
- **THEN** the output recommends `--body-file` for long or code-like bodies and mentions command-text hooks
