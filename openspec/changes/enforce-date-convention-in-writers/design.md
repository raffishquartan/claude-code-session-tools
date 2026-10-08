## Context

The convention (decided 2026-10-05, confirmed 2026-10-06) has three clauses: `yyyy.MM.dd` in
filenames and folder names, ISO `yyyy-MM-dd` everywhere else (`yyyy-MM-dd HH:mm` for time of day,
ISO 8601 `T`/`Z` for machine timestamps), long form only in a formal letter header. Release 3.12
added the managed `date-format` CLAUDE.md section, ISO human-facing output, the pdata write-path
warning and the dotted/compact/long-form readiness findings. This change audits what remains.

## Audit result

Every date-format string under `src/` was classified against the convention on 2026-10-08.

| Group | Files | Verdict |
|---|---|---|
| Stored/exchanged machine ISO 8601 `%Y-%m-%dT%H:%M:%SZ` | telemetry, command cache, scheduler (`state`, `cursor`, `registry`, `surface`), messaging (`service`, `repository`, `retention`), `sessions_db`, `proc_lock`, `install_sync`, `init_service`, `ccsched`, `migrate_ccsched` | exempt: machine ISO 8601 |
| Machine ISO with milliseconds `%Y-%m-%dT%H:%M:%S.%f` | `move-session` script (matches Claude Code transcript timestamps) | exempt: machine ISO 8601 |
| Session tag and branch-style identifiers `%Y%m%d` | `ccd`, `session_tag` hook, `ccs` tag-prefix filters | exempt: identifier containing a date |
| Backup/snapshot stamps `%Y%m%d%H%M%S`, `%Y%m%d-%H%M%S`, `%Y%m%dT%H%M%SZ`, tag `%Y%m%d-%H%M` | `ccst` skill backups, `pdata/backup.py`, `migrate_*`, `messaging/store`, `move-session`, `clean-hook-sessions`, `bash_security_review` | exempt: backup stamp / identifier |
| Human-facing `%Y-%m-%d %H:%M`, `HUMAN_UTC_FORMAT`, `%Y-%m-%d`, `%Y-%m`, `%Y` | `ccr`, `ccs`, `ccst`, `pdata/dump`, `clean-hook-sessions`, `claude_code_usage/query`, `timefmt` | conforms |
| Time of day only `%H:%M` | `context_window_warning` hook | exempt: no date |
| Year/month folder `%Y`, `%m` | `pdata/reorganize` | conforms (folder names `yyyy`, `mm`) |
| User input parsing `%Y%m%d`, `%Y-%m-%d`, `%Y-%m-%dT%H:%M[:%S]` | `ccs --since/--before` | exempt: accepts input, writes nothing |
| Detection parsing `%Y%m%d` | `pdata/readiness` | exempt: reads, writes nothing |
| `extracted_at: datetime.now(UTC).isoformat()` | `extract-to-text` | exempt: machine ISO 8601 with offset |

Nothing in `src/` writes `yyyyMMddTHHmm`, dotted dates outside filenames, slash or long-form
dates. The maxella request to update "writers and parsers" of that form therefore has no code to
change; the gaps are in detection and skill text, below.

## Decisions

### Readiness scan recognises compact timestamps

`_DATE_FORMATS` gains `("compact-datetime", ^\d{8}T\d{4}(\d{2})?Z?$)` placed before the
`yyyymmdd` fallback, and a value matches only when its date part is a real date in 1900..2100
(reusing `_is_real_yyyymmdd`) and its time part is a real time (`HH` 00-23, `mm` 00-59, `ss` 00-59).
It is not in `_ISO_DATE_FORMATS`, so a column of them raises `non-iso-dates`, and mixed with ISO
values also `mixed-date-formats`. Alternative considered: matching the regex only. Rejected,
because the existing `yyyymmdd` format already validates the calendar date to avoid flagging ID
columns, and the same false-positive risk applies here.

### A guard test, not a lint rule

A test under `tests/` parses every `.py` file under `src/` with `ast`, collects string constants
that contain a `%` directive from `{Y, m, d, H, M, S, f}` (this catches `strftime`, `strptime`, SQL
`strftime(...)` text and module constants such as `_TS_FMT`) plus f-string format specs such as
`{x:%Y%m%d}`, and compares the set of `(relative path, format string)` pairs with an allowlist in
the test file. Each allowlist entry carries a reason from a fixed vocabulary (`machine-iso`,
`identifier`, `backup-stamp`, `human-iso`, `time-only`, `input-parse`, `detection-parse`).
Failing in both directions (unlisted format, stale entry) keeps the list honest. Alternatives
considered: a ruff custom rule (no such facility for string contents), and grepping in CI (cannot
distinguish SQL text from prose). Pairs rather than line numbers, so unrelated edits do not churn
the list.

### Skill text: one filename pattern

`pm-update-central-files` Step 1b gives `<yyyy.MM.dd> <HHmm> - <sender> <channel> to
<recipient>.<ext>`, while `pm-project-layout-reference` section 6 and the convention give
`<yyyy.MM.dd>-<HHmm>--<participants>--...`. Both skills are changed to the convention's pattern.
The correspondence-archiving skill that names the files is outside this repo; the implementer
SHALL read its current naming rule before editing and use that rule, not the example in the
convention document, if the two differ.

## Risks and open points

- The correspondence pattern in the skill text may not match what the external archiving skill
  produces. Mitigation: verify against it first (task 3.1); stop and ask if it differs from the
  convention.
- The guard test fails on the first unlisted format added by any future change. This is the
  intended cost; the failure message states how to add an entry.
- Version: patch bump, because the readiness scan only gains a recognised format and a finding on
  data it previously ignored. Existing reports for a column of compact timestamps change from
  silent to a `non-iso-dates` finding; this is the intended fix, not a new interface.
