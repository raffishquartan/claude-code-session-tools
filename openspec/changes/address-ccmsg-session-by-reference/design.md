## Context

`ccmsg send` stores the `--to-session` string verbatim; delivery later matches it against the
reading session's uuid. The sessions store (`sessions` table: project directory, name, uuid) is
populated when `ccd`/`ccr` open a session, so a name or uuid prefix can be resolved there. The
sender's request listed four options: (1) `session_uuid` in `ccs --json`, (2) `--to-tag`,
(3) `--to-session` accepting a name or prefix, (4) the sender uuid in received headers.

## Decisions

### Option 3 plus option 4; not options 1 and 2

One flag that accepts all forms keeps the recipient group (`--to-session`/`--to-project`/
`--to-description`) at three members and adds nothing to learn. `--to-tag` would duplicate it.
`ccs --json` results are per `cc-sessions/` directory and a forked session has several uuids for
one directory, so a single `session_uuid` field would be wrong or need a list; the lookup the
sender needs is a send-time concern. Option 4 is one output line, because `from_uuid` is already
stored with every message and only `read` omits it.

### Resolution order and rules

1. A canonical uuid (`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`,
   case-insensitive) is used as given. Sessions are recorded in the store only when opened through
   `ccd`/`ccr`, so a valid uuid of a session the store has not seen must still be accepted.
2. Otherwise an exact name match via `sessions_db.find_exact(basename)`.
3. Otherwise a prefix: `^[0-9a-f]{8}` followed by hex and dashes, matched with
   `uuid LIKE '<prefix>%'` (prefix escaped), against distinct uuids.
4. Exactly one distinct uuid across matches resolves; zero or several is an error. Several rows
   with one uuid (the same session recorded under two project directories) count once.
5. Anything else is an error naming the accepted forms.

Name before prefix, because a date-prefixed name such as `20261005-fade` is also valid hex
characters; an exact name is the more specific claim.

### Where the code lives

A pure resolver function `resolve_session_ref(ref, rows_lookup)` in a new module
`lib/messaging/session_ref.py`, taking small lookup callables from `sessions_db` so it is testable
without a database. `sessions_db` gains `find_by_uuid_prefix(prefix)` next to `find_exact`.
`ccmsg._resolve_recipient` calls it for the `session` kind and raises `ValueError` (already mapped
to exit 2) with the candidate list. Validation stays at this boundary; `service.send` receives the
full uuid and is unchanged.

### Compatibility

Previously any string was accepted. A value that is not a canonical uuid and resolves to nothing
could never be delivered, so rejecting it only converts silent loss into an error. Existing tests
that pass a placeholder (`target-uuid`) are updated to a canonical uuid. Minor version bump.

## Risks

- The sessions store is empty or missing (fresh install, `ccd` never run): names and prefixes give
  "no session matches", full uuids still work. The error text says the store only knows sessions
  opened with `ccd`/`ccr`.
- A forked session cannot be addressed by name: the error lists the uuids, and the sender picks
  one. Choosing silently would send to the wrong fork.
