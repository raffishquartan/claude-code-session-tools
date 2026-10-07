## Why

Sessions across projects now share one date convention: `yyyy.MM.dd` for filenames and folder
names, long form only in the header of a formal external letter, and ISO `yyyy-MM-dd` for
everything else (time of day `yyyy-MM-dd HH:mm`, machine timestamps ISO 8601). Nothing in CCST
tells sessions this rule, `ccst pdata readiness-scan` cannot see the dotted `yyyy.MM.dd` form
that earlier project conversions introduced into data columns, and a few CCST messages print
timestamps in a form that matches neither the human rule nor a plain ISO date.

## What Changes

- Add a `date-format` section to the CCST-managed CLAUDE.md content (sentinel pair
  `CCST:date-format`), registered alongside `messaging`, `workflow`, `agents` and `confirm-gate`.
  It states the three clauses, the time-of-day rule, the exemptions in one line each (evidence and
  copied files, tool-generated names, identifiers that contain a date, machine ISO fields), and
  "never mix formats inside one column or one field".
- Make `ccst` human-facing messages that currently print a machine-style `T...Z` timestamp print
  `yyyy-MM-dd HH:mm` (UTC where the source is UTC, marked as such). Scope after checking the
  earlier writer sweep against the final rule:
  - `ccst doctor` reason text (`lib/doctor.py` `_fmt_epoch` and the failed-attempt text at the
    cutover/last-run lines, and the auto-sync failure text in `lib/install_sync.py`).
  - The pdata cutover migration log line (`lib/pdata/cutover.py`).
  - The `clean-hook-sessions` table (`human_time`).
  - `ccmsg read`, which prints the stored `sent_at` machine timestamp verbatim; it renders the
    time as `yyyy-MM-dd HH:mm UTC` (the stored value is unchanged).
- Leave everything the sweep listed that is already compliant: `ccr`, `ccs`, `ccst` `_fmt_ts`,
  `format_dumped_at`, `claude-code-usage` day/month labels and the `analyse-cc-usage` skill text
  already print `yyyy-MM-dd[ HH:mm]`; the sweep proposed dotted forms under a rule that has since
  been replaced. Machine timestamps stored or exchanged as ISO 8601 (message, scheduler, telemetry,
  command cache, transcript fields) are unchanged.
- `ccst pdata readiness-scan`: recognise `yyyy.MM.dd` as a date format so a column mixing it with
  ISO values raises `mixed-date-formats`. Compact (`yyyyMMdd`) and long-form (`6 Sep 2024`,
  `Sep 6, 2024`) values are already recognised; add tests that pin a column mixing each of them
  with ISO values to a finding. Add a new column-level finding kind, `non-iso-dates`, for a column
  that holds any dotted, compact, slash or long-form value, including a column that uses one such
  format throughout (which `mixed-date-formats` cannot see).
- `ccst pdata add` and `ccst pdata update`: print a warning on stderr (the write still succeeds)
  when a value written to a date-like field is not an ISO date form. A field is date-like when its
  name ends `_at` or `_date`, or its description mentions "date"; a name ending `_text` (verbatim
  text) is never date-like.
- Tests for every change, docs, a CHANGELOG entry and a minor version bump (new capability, no
  breaking change).

## Capabilities

### New Capabilities
- `cli/human-facing-date-format`: dates and times that CCST commands print for a human, outside
  filenames and outside machine fields, use `yyyy-MM-dd` and `yyyy-MM-dd HH:mm`.

- `pdata/date-field-warning`: a non-blocking warning when a non-ISO value is written to a
  date-like pdata field.

### Modified Capabilities
- `install/claude-md-fragment`: the registry of managed sections gains `date-format`, with its
  content contract.
- `pdata/readiness-scan`: the recognised date formats gain dotted `yyyy.MM.dd`, and a
  `non-iso-dates` finding kind is added.

## Impact

- `src/cc_session_tools/lib/claude_md_install.py` (new section body and registry entry) and
  `tests/test_claude_md_install.py`.
- `src/cc_session_tools/lib/doctor.py`, `lib/install_sync.py`, `lib/pdata/cutover.py`,
  `skills/clean-hook-sessions/scripts/clean-hook-sessions.py` and their tests.
- `src/cc_session_tools/lib/pdata/service.py` (a date-field check), the `pdata add`/`update`
  handlers in `cli/ccst.py`, `cli/ccmsg.py`, and their tests.
- `src/cc_session_tools/lib/pdata/readiness.py`, its tests, and the
  `pm-pdata-do-audit-and-prepare-to-migrate` skill text if it lists recognised formats.
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (minor bump).
- After release, the next `ccst` run syncs the new section into the installed user-level
  CLAUDE.md through the existing auto-sync; no manual edit of that file.
