# cli/ccmsg-message-age Specification

## Purpose

Lets `ccmsg list` show staleness at a glance, using the message age information already stored
but not previously surfaced in that listing.

## Requirements

### Requirement: `ccmsg list` shows each message's age
Each row `ccmsg list` prints SHALL include the message's age, expressed the same way the
delivery digest already expresses it (e.g. "3m ago", "2h ago", "5d ago").

#### Scenario: A recently sent message shows a short relative age
- **WHEN** a message was sent a few minutes ago
- **THEN** its row in `ccmsg list` shows an age in minutes

#### Scenario: An old message shows a longer relative age
- **WHEN** a message was sent several days ago
- **THEN** its row in `ccmsg list` shows an age in days, not minutes or hours
