# cli/bundled-pm-skills Specification

## Purpose

Defines which `pm-*` skills this repo bundles and installs via its standard skill-sync mechanism,
so a skill built and proven useful on one machine becomes available on every machine that
installs this package, not just the one it was authored on.

## Requirements

### Requirement: `pm-pdata-audit` and `pm-pdata-migrate` are bundled skills
The `pm-pdata-audit` and `pm-pdata-migrate` skills SHALL be bundled under this package's skills
directory and installed by `ccst skills install` / `ccst install-everything`, the same mechanism
that installs every other `pm-*` skill.

#### Scenario: A fresh install provisions both skills
- **WHEN** `ccst install-everything --apply` (or `ccst skills install`) runs on a machine that has
  never had these skills installed
- **THEN** `pm-pdata-audit` and `pm-pdata-migrate` are symlinked into the target skills directory,
  the same way `pm-project-init` and this package's other bundled `pm-*` skills are

#### Scenario: Both skills stay in sync on subsequent installs
- **WHEN** `ccst skills install` runs again after this package is upgraded
- **THEN** `pm-pdata-audit` and `pm-pdata-migrate` are kept in sync with the bundled copy, exactly
  as every other bundled skill already is
