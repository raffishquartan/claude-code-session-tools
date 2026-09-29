## ADDED Requirements

### Requirement: Pending-migration guidance for `sessions` names a working order
When the legacy flat-file import and the uuid schema migration are both pending for `sessions`,
`ccst doctor` SHALL report both findings from the same `sessions.db`, and each finding's guidance
SHALL say that the rebuild and the import go together and that `ccst migrate all` performs them in
the working order. Guidance SHALL NOT direct the user to a command that would refuse or fail in
the current state.

#### Scenario: Legacy import FAIL notes it will rebuild first
- **WHEN** legacy sessions data is unmigrated and `sessions.db` exists with the pre-uuid schema
- **THEN** the `migration-to-1.0.0:sessions` finding is a FAIL whose reason names
  `ccst migrate all` and states that it first rebuilds `sessions.db` (taking a backup)

#### Scenario: Legacy import FAIL is unchanged when the schema is already current
- **WHEN** legacy sessions data is unmigrated and `sessions.db` is absent or already uuid-aware
- **THEN** the `migration-to-1.0.0:sessions` finding reads as before

#### Scenario: Schema-migration FAIL mentions the pending import
- **WHEN** `sessions.db` is pre-uuid (in either failing state of the 3.0.0 check) and legacy
  sessions data is also unmigrated
- **THEN** the `migration-to-3.0.0:sessions-uuid` FAIL states that the legacy import is also
  pending and that `ccst migrate all` performs both
