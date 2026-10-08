## Why

Projects now convert their stored data to the shared date convention (filenames `yyyy.MM.dd`,
everything else ISO, time of day `yyyy-MM-dd HH:mm`, machine timestamps ISO 8601). Release 3.12
made CCST print human-facing times in that form and added date checks to the pdata write path and
readiness scan, but nothing guarantees that every other date CCST writes follows the rule, and a project that converted a column to
`yyyy-MM-dd HH:mm` (maxella) asked that all writers be checked. An audit of `src/` against the
convention found the code writers compliant or exempt, plus two real gaps:

- `ccst pdata readiness-scan` does not recognise compact timestamps (`20261008T1057`,
  `20261008T105743Z`), so a column of them raises no finding while a column of compact dates does.
- Nothing stops a new `strftime` call from reintroducing a non-conforming format.

## What Changes

- `ccst pdata readiness-scan`: recognise compact timestamps `yyyyMMddTHHmm`, `yyyyMMddTHHmmss` and
  the same with a trailing `Z` as a date format (`compact-datetime`). A column using one of them
  throughout raises `non-iso-dates`; a column mixing one with ISO values also raises
  `mixed-date-formats`.
- A guard test lists every date format string in `src/` (`strftime`, `strptime`, f-string format
  specs) against an allowlist, each entry carrying its exemption reason (machine ISO 8601,
  identifier containing a date, backup stamp, tool-generated name, human-facing ISO form). A new
  format string not on the list fails the test.
- Document the audit result: the exemption list is recorded in `design.md` so the next reviewer
  does not re-derive it.
- Tests for each change, a CHANGELOG entry and a patch version bump (bug fix, no interface change).

Out of scope, and why:

- The convention document under the `claude` project's sessions (its section 4 maxella row says
  "leave stored values", contradicting its sections 1 and 5). It is not in this repo; the owner of
  that file should update it.
- Skills that live outside this repo (for example `archive-correspondence`, `gmail-compose`).
  Only `src/cc_session_tools/skills/` is covered.
- The correspondence filename pattern in `pm-update-central-files` (`<yyyy.MM.dd> <HHmm> - ...`).
  It matches what the external `archive-correspondence` skill produces, so changing only this
  skill would break its audit. The convention document's example shows `yyyy.MM.dd-HHmm--...`;
  both use the dotted date part that filenames require. Which separator style files use is for the
  owner of the archiving skill to decide, and this skill follows once it does.
- Converting any project's stored data; that stays with each project's own conversion session.
- Compact stamps that are identifiers or backup names (`yyyyMMdd-<tag>`, `...bak-yyyyMMddHHmmss`,
  `yyyyMMddTHHmmssZ` migration backups): exempt under the convention.

## Capabilities

### New Capabilities
- `cli/date-writer-conformance`: every date or time CCST code writes is a convention form or a
  listed exemption; a test pins the set of format strings in `src/`.

### Modified Capabilities
- `pdata/readiness-scan`: the recognised date formats gain compact timestamps.

## Impact

- `src/cc_session_tools/lib/pdata/readiness.py` and `tests/` for the readiness scan.
- `src/cc_session_tools/skills/pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md` (recognised
  formats list).
- New `tests/test_date_format_conformance.py` and its allowlist.
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (patch bump).
