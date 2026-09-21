## ADDED Requirements

### Requirement: The audit skill runs the readiness scan before dispatching agents
`pm-pdata-do-audit-and-prepare-to-migrate`'s `SKILL.md` SHALL instruct the session to run
`ccst pdata readiness-scan` on the project before dispatching any audit agent, and to pass each
agent only the scan output for that agent's own domain (`--path`). It SHALL limit agent checklists
to checks the scan cannot make: file-to-index correspondence, controlled-vocabulary compliance,
cross-references, documented-but-missing files, derived-artefact completeness, documentation
accuracy, multi-schema single files, comma/semicolon list-column separators, and the judgement of
whether a unique column is a stable key. It SHALL retain the warning that `row_id`-style columns
numbered independently by different sessions or machines are not stable keys, and state that the
scan's unique columns are input to that judgement, not its answer. It SHALL make parallel agents
conditional on the number of audit domains rather than mandatory, SHALL keep the Phase 5 manifest
layout and Phase 3 checkpoint-table split, and SHALL keep handing deletions back as a reviewable
script rather than running them.

#### Scenario: A session starts the audit
- **WHEN** a session follows the skill from its first phase
- **THEN** the scan runs before any per-domain checking begins and before any audit agent is
  dispatched (after the sync-conflict cleanup, when the project is in a synced folder)

#### Scenario: A small project
- **WHEN** the project has three or fewer audit domains
- **THEN** the skill directs the session to audit them itself in one pass rather than dispatching
  one agent per domain

#### Scenario: The row_id warning survives
- **WHEN** a session reads the rewritten skill
- **THEN** it still finds the warning about independently-numbered `row_id`-style columns, in text
  that `pm-pdata-do-manual-migration`'s cross-reference to it still resolves to
