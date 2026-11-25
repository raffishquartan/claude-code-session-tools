## ADDED Requirements

### Requirement: Help defines every message status
`ccmsg --help` and `ccmsg list --help` SHALL both print the same definition of the statuses `sent`, `read`, `claimed` and `archived`, taken from one definition in the code. The definition SHALL state:

- `sent`: stored and not yet shown to a recipient; a description-addressed message stays `sent` until it is claimed.
- `read`: a session-start or prompt digest showed a session- or project-addressed message to its recipient (`ccmsg read` does not change status); read messages are listed again at every session start under "Read but not yet handled" until claimed or archived.
- `claimed`: `ccmsg claim` marked the message handled; the first claim wins, so a claimed project message stops listing for every session of the project and a description-addressed message can be taken by one session only.
- `archived`: `ccmsg archive` discarded the message, or retention archived a claimed message at least 14 days after its claim; unclaimed messages are never archived automatically.
- `ccmsg unarchive` restores an archived message that was never claimed (read before archiving: back to `read`; never read: back to `sent`); a message that was claimed cannot be restored.

#### Scenario: Top-level help lists the statuses
- **WHEN** `ccmsg --help` is run
- **THEN** the output defines `sent`, `read`, `claimed` and `archived`

#### Scenario: List help lists the statuses
- **WHEN** `ccmsg list --help` is run
- **THEN** the output contains the same status definitions as `ccmsg --help`
