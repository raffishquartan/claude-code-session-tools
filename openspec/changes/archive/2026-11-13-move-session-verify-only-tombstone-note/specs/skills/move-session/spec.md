## ADDED Requirements

### Requirement: Verify-only output explains a tombstone-caused line-count FAIL
When `--verify-only` finds the source transcript longer than the destination, the verification output SHALL state the number of extra source lines. If the source ends with tombstone records that account for those lines, it SHALL state that the FAIL on line count is expected after a move with the tombstone enabled and why (the tombstone is appended to the source after the copy), and that the other verification rows are the ones to check. If the extra lines are not accounted for by tombstone records, it SHALL say they are unexplained. The `RESULT` value and the exit code SHALL be unchanged.

#### Scenario: Move without rename, tombstone enabled
- **WHEN** `--verify-only` runs after a MOVE that appended tombstone records to the source
- **THEN** the output reports `RESULT : FAIL`, names the extra source lines, and states that the line-count FAIL is expected because of the tombstone

#### Scenario: Unexplained extra source lines
- **WHEN** the source has more lines than the destination and no tombstone records account for them
- **THEN** the output says the extra lines are unexplained and does not call the FAIL expected

#### Scenario: Destination not shorter
- **WHEN** the source has no more lines than the destination
- **THEN** no explanation is printed
