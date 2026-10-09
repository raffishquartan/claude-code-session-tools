## Context

`init_service.dry_run` decides newness once, before `resolve_project_root` creates the root, and
calls `init_paths.scaffold_new_project_dirs` for a new project only. A rehearsal target and any
project whose root exists are never "new". The starter-folder rule is the model: create once, never
touch an existing project, never recreate what the user removed.

## Decisions

### Same trigger and call site as the starter folders

The CLAUDE.md is written inside the existing `if is_new_project:` branch, by extending the scaffold
helper rather than adding a second newness check. `scaffold_new_project_dirs` is renamed
`scaffold_new_project` and takes the project name, because the heading needs it; its only caller
is updated in the same change. Writing is `write_text(..., newline="\n")` through
`Path.write_bytes`-equivalent behaviour so the seed itself has LF endings.

The file is written only when absent. For a genuinely new project it always is; the guard makes the
helper safe if it is ever called on a root that has one, instead of overwriting.

### Seed text lives in one module, with the wording the requester settled on

A new module `lib/pdata/project_claude_md.py` holds the text as constants and one function
`seed_text(project)`. The wording follows the requester's finished file, minus its final bullet
that links to a copy of the convention document on a personal drive: this repository is public and
the managed `date-format` section of the user-level CLAUDE.md already carries the rule for
sessions. The managed section's body is not reused because it addresses a session ("Every date a
session writes") and ends with a paragraph about conversion tasks that does not apply to an empty
project.

### Tests

- Fresh project (dry-run and `--write`): file present, heading, both section headers, no `\r`,
  no `/Users/` or `/home/`.
- Existing project without a CLAUDE.md: still none; existing with content: unchanged bytes.
- Second run after deletion: not recreated; after edit: kept.
- Rehearsal target: untouched.

## Risks

- The seeded wording duplicates part of the managed `date-format` section. If the convention
  changes, both change. Accepted: the managed section targets sessions, the seed targets a
  project's own document, and a test pins that the seed names the three filename/ISO clauses so a
  rule change fails loudly.
- `scaffold_new_project_dirs` is renamed; any external import of it breaks. It is internal to
  `lib/pdata`, with one caller.
