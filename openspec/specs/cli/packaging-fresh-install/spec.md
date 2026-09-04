# cli/packaging-fresh-install Specification

## Purpose

Defines what a fresh install of this package (a machine with no prior editable checkout) must be
able to locate on disk, so packaging regressions in bundled data (skills, hook config, prompts)
are caught by an automated test rather than discovered by a user's first `ccst` invocation.

## Requirements

### Requirement: A fresh install locates all bundled data directories
Installing this package into a clean environment (no source checkout, no editable install) via a
built wheel SHALL result in `ccst`'s bundled-data discovery finding the `skills/`, `config/`, and
`prompts/` directories packaged inside the installed `cc_session_tools` distribution, with no
manual `--source` override required.

#### Scenario: `ccst install-everything` on a clean install
- **WHEN** the package is installed into a fresh virtual environment from a built wheel (no
  editable/source-tree install) and `ccst install-everything --apply` (or an equivalent command
  that reads bundled skills/config/prompts) is run
- **THEN** it locates the bundled `skills/`, `config/`, and `prompts/` directories inside the
  installed distribution and completes without a "cannot locate bundled ..." error

### Requirement: Bundled-data discovery has automated regression coverage
The bundled-data discovery functions (`_discover_source_dir`, `_discover_bundle`, and their
`prompts/`-directory equivalent) SHALL have a test that exercises discovery against an installed
package layout, not only against the source-tree/editable-install layout.

#### Scenario: Regression test exists and passes
- **WHEN** the test suite runs
- **THEN** a test simulates or verifies package-relative discovery of `skills/`, `config/`, and
  `prompts/` succeeding from an installed-distribution layout
