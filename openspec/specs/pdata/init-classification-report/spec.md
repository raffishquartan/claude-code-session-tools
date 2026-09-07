# pdata/init-classification-report Specification

## Purpose

Defines an automatic heuristic in `ccst pdata init`'s dry-run classification report that flags a
`db-owned` entry whose proposed field names look like garbled sentence fragments rather than real
column names, so a reviewer's attention is drawn there without depending on an independent manual
skim catching the same pattern.

## Requirements

### Requirement: The dry-run report flags entries with suspicious field-name shapes
When the dry-run classification report includes a `db-owned` entry with multiple proposed field
names that exceed a length or word-count threshold indicating they read as sentence fragments
rather than short domain nouns, the system SHALL annotate that entry in the printed report as worth
double-checking, distinct from an ordinary `db-owned` entry line.

#### Scenario: A garbled-header entry is flagged
- **WHEN** a dry-run classification report includes a `db-owned` entry whose proposed fields
  include names such as `pending_chris_s_manual_review` (multiple long, many-word field names)
- **THEN** that entry's line in the printed report is annotated as worth double-checking, calling
  out that the file's header may not be what it looks like

#### Scenario: A normal entry is not flagged
- **WHEN** a dry-run classification report includes a `db-owned` entry whose proposed field names
  are short, ordinary column names
- **THEN** that entry's line in the printed report carries no such annotation
