# cli/ccmsg-project-path-recipient Specification

## Purpose
Lets a sender address a project by the folder it knows, resolving the path with the same rule a session uses for its own cwd so sender and recipient agree on the project.

## Requirements

### Requirement: `--to-project` accepts a folder path
`ccmsg send --to-project <value>` SHALL treat a value that starts with `/` or `~` as a folder path and any other value as a project name, unchanged from before. A path SHALL be `~`-expanded and resolved, then mapped to the project folder that contains it: the path itself or its nearest ancestor that is a direct child of a configured session root. The message SHALL be stored with that folder's name as the recipient project, in the same store partition a session started in that folder sweeps, so sender-side and recipient-side project resolution agree. A path inside a project (a subfolder or a `cc-sessions` folder) SHALL resolve to the same project as the project folder.

When no such project folder exists (the path is outside every configured root, is a root itself, or no roots are configured), `ccmsg send` SHALL exit non-zero, print an error naming the path, and store nothing.

#### Scenario: Absolute path
- **WHEN** `--to-project` is the absolute path of a project folder under a configured root
- **THEN** the message is stored for that project and `ccmsg list` shows `project=<folder name>`

#### Scenario: Home-relative path
- **WHEN** `--to-project` is a `~`-prefixed path to a project folder
- **THEN** the message is stored for that project, as for the absolute path

#### Scenario: Subfolder of a project
- **WHEN** `--to-project` is a path inside a project folder, such as its `cc-sessions/<tag>` folder
- **THEN** the message is stored for the containing project

#### Scenario: Unknown path
- **WHEN** `--to-project` is a path that is not inside any project folder of a configured root
- **THEN** `ccmsg send` exits non-zero with an error naming the path and stores no message

#### Scenario: Plain name
- **WHEN** `--to-project` is a plain project name
- **THEN** the message is stored for that name exactly as before
