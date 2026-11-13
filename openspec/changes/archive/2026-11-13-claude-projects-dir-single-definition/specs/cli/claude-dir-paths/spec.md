## ADDED Requirements

### Requirement: One definition of the Claude Code transcript root

The path `~/.claude/projects` SHALL be built only by `lib/paths.claude_projects_dir()`, resolved on each
call. Package code SHALL NOT build it from `Path.home()` itself.

#### Scenario: Usage CLI default follows a redirected home
- **WHEN** `Path.home()` is redirected and `claude-code-usage` parses arguments without `--projects-dir`
- **THEN** the default projects directory is under the redirected home

#### Scenario: Hook transcript lookup follows a redirected home
- **WHEN** `Path.home()` is redirected and the hook transcript loader looks up a session
- **THEN** it reads `<redirected home>/.claude/projects/<encoded cwd>/<session>.jsonl`

#### Scenario: No second definition in the source tree
- **WHEN** the source tree under `src/` is scanned for the literal `".claude" / "projects"`
- **THEN** the only match is in `lib/paths.py`
