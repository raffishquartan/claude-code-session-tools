## MODIFIED Requirements

### Requirement: `pm-pdata-audit` and `pm-pdata-migrate` are bundled skills
<!-- Requirement renamed on merge to "`pm-pdata-do-audit-and-prepare-to-migrate` and
     `pm-pdata-do-manual-migration` are bundled skills" - kept as the original header here so
     the sync step locates the existing main-spec requirement; the main spec's own name for
     this requirement changes to match its updated body. This corrects pre-existing drift: the
     main spec already named the pre-3.1.0 identifiers `pm-pdata-audit`/`pm-pdata-migrate`, not
     even today's current `pm-pdata-do-audit-and-prepare-to-migrate`/`pm-pdata-do-migrate`. -->
The `pm-pdata-do-audit-and-prepare-to-migrate` and `pm-pdata-do-manual-migration` skills SHALL be
bundled under this package's skills directory and installed by `ccst skills install` /
`ccst install-everything`, the same mechanism that installs every other `pm-*` skill.

#### Scenario: A fresh install provisions both skills
- **WHEN** `ccst install-everything --apply` (or `ccst skills install`) runs on a machine that has
  never had these skills installed
- **THEN** `pm-pdata-do-audit-and-prepare-to-migrate` and `pm-pdata-do-manual-migration` are
  symlinked into the target skills directory, the same way `pm-project-init` and this package's
  other bundled `pm-*` skills are

#### Scenario: Both skills stay in sync on subsequent installs
- **WHEN** `ccst skills install` runs again after this package is upgraded
- **THEN** `pm-pdata-do-audit-and-prepare-to-migrate` and `pm-pdata-do-manual-migration` are kept
  in sync with the bundled copy, exactly as every other bundled skill already is
