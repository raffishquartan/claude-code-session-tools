## ADDED Requirements

### Requirement: Installing records what it wrote
When `install_claude_md` writes a managed section block, it SHALL record a hash of the written block text, keyed by the target CLAUDE.md path and the section id, in the shared sessions store. It SHALL NOT record anything on a dry run, and SHALL remove the record when the section is uninstalled.

#### Scenario: Record written on apply
- **WHEN** a section is installed or replaced with `--apply`
- **THEN** the hash of that block's text is recorded for that file and section

#### Scenario: Dry run records nothing
- **WHEN** install runs without `--apply`
- **THEN** no hash is recorded or changed

#### Scenario: Uninstall clears the record
- **WHEN** a section is uninstalled with `--apply`
- **THEN** its recorded hash is removed

### Requirement: Hand-edited blocks are backed up before overwrite
Before replacing an existing managed block with different text, or removing it on uninstall, install and uninstall SHALL compare the existing block with the recorded hash. When the block does not match the recorded hash (including when no hash is recorded), install SHALL save the existing block text as a backup, retrievable by id, before overwriting it. A block that matches the recorded hash is an out-of-date template, not a customisation, and SHALL be replaced without a backup. A dry run SHALL report that a backup would be taken and SHALL NOT write one. If the sessions store cannot be used (corrupt, locked or unwritable), the install or uninstall SHALL still proceed and the result SHALL carry the reason the text could not be saved.

#### Scenario: Hand-edited block is backed up
- **WHEN** a managed block was edited by hand after ccst wrote it and install replaces it with `--apply`
- **THEN** the pre-replacement block text is stored as a backup and then replaced

#### Scenario: Out-of-date template is not treated as an edit
- **WHEN** the block on disk equals the text ccst last wrote but the current template differs
- **THEN** the block is replaced and no backup is stored

#### Scenario: No record yet
- **WHEN** a differing block has no recorded hash (first run after this feature ships)
- **THEN** the block is backed up before replacement

#### Scenario: Dry run
- **WHEN** install runs without `--apply` against a hand-edited block
- **THEN** the result says a backup would be taken and no backup or file change is made

#### Scenario: Uninstall of a hand-edited block
- **WHEN** uninstall with `--apply` removes a hand-edited block
- **THEN** its text is backed up first and the removal is reported with the backup id

#### Scenario: Unusable store
- **WHEN** the sessions store cannot be opened or written during an apply
- **THEN** the CLAUDE.md change still happens and the warning says the previous text could not be saved

### Requirement: Overwrites of hand-edited blocks are reported
Each overwrite that took a backup SHALL appear in the install result with the section id and backup id. The claude-md step of `ccst install-everything` SHALL print a warning line for each, and the automatic post-upgrade sync SHALL print the same information to stderr, never stdout.

#### Scenario: Warning on explicit install
- **WHEN** `ccst claude-md install --apply` overwrites a hand-edited block
- **THEN** the output names the section and the backup id and says the previous text can be read with `ccst claude-md backups show`

#### Scenario: Warning on auto-sync
- **WHEN** the automatic sync overwrites a hand-edited block
- **THEN** a warning naming the section and backup id is written to stderr and stdout is unchanged

### Requirement: Doctor warns about pending hand edits
`ccst doctor` SHALL include a check that WARNs, naming the section, for each installed managed block whose text differs from both the current template and the recorded hash. It SHALL be OK when every installed block matches the template or the recorded hash.

#### Scenario: Pending hand edit
- **WHEN** a managed block in the CLAUDE.md differs from the template and from its recorded hash
- **THEN** the doctor check WARNs naming that section and saying the next install will back it up and overwrite it

#### Scenario: No drift
- **WHEN** every installed block matches the template
- **THEN** the doctor check is OK

### Requirement: Backed-up blocks can be listed and read
`ccst claude-md backups list` SHALL list stored backups (id, timestamp, file, section). `ccst claude-md backups show <id>` SHALL print the stored block text, and SHALL fail with a clear message for an unknown id. Both SHALL fail with a message pointing to `ccst repair sessions` when the store is corrupt, rather than reporting no backups.

#### Scenario: List and show
- **WHEN** a backup exists and the user runs `list` then `show <id>`
- **THEN** the backup appears in the list and `show` prints exactly the backed-up block text

#### Scenario: Unknown id
- **WHEN** `show` is given an id that does not exist
- **THEN** it exits non-zero with a message naming the id
