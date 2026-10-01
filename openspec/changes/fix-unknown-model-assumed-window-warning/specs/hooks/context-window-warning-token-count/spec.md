## ADDED Requirements

### Requirement: A real entry with no model id is measured, not skipped
A non-sidechain entry with a usage block and an empty or missing model (not `<synthetic>`) SHALL
still supply the token count, and the model id SHALL be treated as unknown for the warning's wording.

#### Scenario: Usage without a model
- **WHEN** the last real assistant entry has 180,000 context tokens and no model field
- **THEN** the warning measures 180,000 tokens and uses the assumed-window wording
