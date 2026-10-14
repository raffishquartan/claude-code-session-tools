## MODIFIED Requirements

### Requirement: The fragment documents session working-file conventions
The CCST-managed CLAUDE.md content SHALL include a `workflow` section documenting that sessions
started via `ccd`/`ccr` get a `working/` and `out/` directory, that a deliverable likely to go
through multiple rounds of iteration should be drafted as a file under `working/` and the
confirmed final version moved to `out/`, that every `ccd`/`ccr` session, including trivial ones,
MUST have a `working/WORKLOG.md` created as the first action of the session and appended to
incrementally with one entry per meaningful step or decision, each headed with
`yyyy-MM-dd HH:mm` (not only when a compact is attempted or when asked), that the `worklog-guard`
hook blocks a manual `/compact` when that file is stale, and that the user may be editing the same
file concurrently (re-read before further edits; never discard a change the user made directly
without being asked).

#### Scenario: `workflow` section installed
- **WHEN** the `workflow` section is written or updated in `~/.claude/CLAUDE.md`
- **THEN** it documents the `working/`→`out/` draft-then-finalize convention, the mandatory
  `working/WORKLOG.md` (first action, incremental, timestamped entries) and its `worklog-guard`
  relationship, and the concurrent-editing caution, within its own
  `<!-- CCST:workflow START/END -->` sentinel pair

#### Scenario: WORKLOG paragraph wording
- **WHEN** the `workflow` section is rendered
- **THEN** its WORKLOG paragraph reads exactly: "Every `ccd`/`ccr` session, including trivial
  ones, MUST have a `working/WORKLOG.md`. Create it as the first action of the session, before
  any other work, and append to it incrementally as the session progresses - not only when a
  compact is attempted or when asked. Add one entry per meaningful step or decision, each headed
  with `yyyy-MM-dd HH:mm`. The `worklog-guard` hook blocks a manual `/compact` when the file is
  stale."
