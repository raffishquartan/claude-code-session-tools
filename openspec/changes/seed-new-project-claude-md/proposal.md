## Why

A project created through `ccst pdata init` starts with the three starter folders and nothing
else, so each new project's first `CLAUDE.md` is written by hand and the same two conventions
(date format and LF line endings) have to be re-added every time. A project that started without
them has already needed a correction pass (CRLF written by Python's `csv.writer` default;
dates written in mixed forms). The request comes from a project that wrote both sections by hand
on 2026-10-09 and asked that every new project start with them (ccmsg
`20261009T121450Z-7151`).

## What Changes

- The first `ccst pdata init` run for a genuinely new project (dry-run or `--write`, same trigger
  as the starter folders) also writes `<project root>/CLAUDE.md` containing a `# <project>`
  heading, a `## Date format` section and a `## Line endings` section.
  - Date format: the three-clause rule (filenames `yyyy.MM.dd`; formal letter headers long form;
    everything else `yyyy-MM-dd`, time `yyyy-MM-dd HH:mm`, machine timestamps ISO 8601), never
    mix formats in one column or field, and the exemptions.
  - Line endings: LF not CRLF; `csv.writer`/`csv.DictWriter` need `lineterminator='\n'`; open text
    files for writing with `newline='\n'`; check a new or rewritten text file has no `\r`.
- An existing project's `CLAUDE.md` is never created or changed by `init`; an existing project
  keeps its structure exactly as it does for the starter folders.
- The seeded text names no person, machine or path and does not link to any file outside the
  project: the repository is public. The worked example's pointer to a personal-drive copy of the
  convention is not carried over.

## Capabilities

### New Capabilities

### Modified Capabilities
- `pdata/init-scaffolding`: a new project also gets a starting `CLAUDE.md`; an existing project's
  `CLAUDE.md` is untouched.

## Impact

- `src/cc_session_tools/lib/pdata/init_paths.py` (scaffold helper) and a new module holding the
  seed text, `src/cc_session_tools/lib/pdata/init_service.py` (call site, unchanged condition).
- `tests/pdata/test_init_paths.py` and the init-service tests for scaffolding.
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (minor bump: new generated file).
- Stacked on `f/20261009-ccmsg-session-ref` (PR 170): the version bump follows that branch's
  3.13.0.
