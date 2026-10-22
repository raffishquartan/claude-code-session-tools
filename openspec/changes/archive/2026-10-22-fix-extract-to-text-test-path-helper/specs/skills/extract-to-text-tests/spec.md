## ADDED Requirements

### Requirement: PATH-hiding test helper tolerates dangling symlinks
The `extract-to-text` test helper that builds a PATH without named binaries SHALL succeed when the real PATH contains a dangling symlink, including the same dangling name in several PATH directories, and SHALL keep the first PATH directory's entry for a duplicated name.

#### Scenario: Dangling link in two PATH directories
- **WHEN** two PATH directories each contain a symlink with the same name whose target does not exist
- **THEN** building the restricted PATH does not raise and the restricted directory holds exactly one link of that name

#### Scenario: Hidden binary
- **WHEN** a binary name is passed to hide
- **THEN** the restricted directory contains no entry of that name
