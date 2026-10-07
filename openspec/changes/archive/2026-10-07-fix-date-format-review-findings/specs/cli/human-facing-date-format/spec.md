## ADDED Requirements

### Requirement: A stored timestamp that cannot be rendered is shown as stored
When a command renders a stored machine timestamp for a human and the stored value is not in the
writer's `yyyy-MM-ddTHH:mm:ssZ` shape, the command SHALL print the stored value unchanged rather
than fail.

#### Scenario: ccmsg read with an unexpected sent_at
- **WHEN** `ccmsg read` prints a message whose stored `sent_at` is `2026-10-05T14:30:00.123+00:00`
- **THEN** the `sent_at` line shows that value as stored and the message body is still printed

### Requirement: One definition of the human UTC time format
The `yyyy-MM-dd HH:mm UTC` rendering SHALL be defined in one module and used by every CCST
command that prints a UTC time for a human.

#### Scenario: Shared format
- **WHEN** `ccst doctor`, the auto-sync failure message, the pdata cutover log and `ccmsg read`
  print a UTC time
- **THEN** each renders it through the shared helper and the output is `yyyy-MM-dd HH:mm UTC`
