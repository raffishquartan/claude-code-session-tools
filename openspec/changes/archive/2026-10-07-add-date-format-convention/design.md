## Context

A universal date convention now applies to files and text written by sessions in every project.
CCST owns three touch points: the managed CLAUDE.md content that tells sessions the rule, the
`readiness-scan` that finds mixed-format data columns before a pdata migration, and the messages
CCST itself prints. An earlier writer sweep of this repo was done under a superseded rule (dotted
dates everywhere) and proposed dotted output for many places; under the final rule most of those
places are already correct.

## Goals / Non-Goals

**Goals:**
- Sessions on any machine that has CCST installed receive the convention through the existing
  managed-section install.
- `readiness-scan` detects a column that mixes ISO values with dotted, compact or long-form values.
- CCST prose output uses `yyyy-MM-dd[ HH:mm]` wherever it is not a machine field.

**Non-Goals:**
- Changing any stored or exchanged timestamp, any identifier containing a date, or any historical
  file, document or archived change directory name.
- Converting project data; each project session does that from its own conversion prompt.
- Editing the user-level CLAUDE.md by hand; the section reaches it only through install/sync.

## Decisions

1. **New section, not an edit of an existing one.** `date-format` is added to `_SECTION_BODIES`
   and `SECTION_IDS` in `lib/claude_md_install.py`, appended last so existing sections keep their
   order and bytes. Alternative (fold into `workflow`) rejected: the sections are independently
   installable and the rule is not about working files. Existing section-registry tests
   (enumerating ids, per-section install/uninstall/idempotency) are extended to include it.

2. **Section text is short and generic.** It states the rule and exemptions in about 15 lines and
   names no person, machine or project (this repo is public). Exemptions are one line each.

3. **Display-only changes in task 2.** Only rendering of an ISO machine value into prose changes;
   the stored value and its parser stay as they are. A shared formatter is not introduced:
   the changed call sites are in different layers and each is a one-line `strftime`, so each uses
   the literal `%Y-%m-%d %H:%M UTC` pattern with its own test.

4. **Task 2 scope after checking each sweep row against the final rule.**
   | Sweep row | Current output | Decision |
   |---|---|---|
   | ccr, ccs, `_fmt_ts`, `format_dumped_at` | `yyyy-MM-dd HH:mm` | already correct, no change |
   | claude-code-usage day/month labels, `analyse-cc-usage` text | `yyyy-MM-dd`, `yyyy-MM` | already correct, no change |
   | `doctor --list-mutes` (`muted_at`) | `yyyy-MM-dd` | already correct |
   | `ccmsg read` `sent_at` | stored `...T...Z` printed verbatim | change the printed line to `yyyy-MM-dd HH:mm UTC`; stored value unchanged |
   | doctor reason text, auto-sync failure text | `yyyy-MM-ddTHH:mm:ssZ` in prose | change to `yyyy-MM-dd HH:mm UTC` |
   | pdata cutover log line | `yyyy-MM-ddTHH:mm:ssZ` | change to `yyyy-MM-dd HH:mm UTC` |
   | `clean-hook-sessions` `human_time` | `isoformat(timespec="seconds")` (`T`) | change to `yyyy-MM-dd HH:mm` |
   | prose dates in comments, docs, CHANGELOG headings | ISO | already correct; Keep a Changelog headings stay ISO |
   | compact stamps (session tags, backups, branch names) | `yyyyMMdd...` | exempt identifiers |

   The second, independent sweep is used at implementation time only to look for hits missed by
   the first; nothing in it is applied without checking it against the rule.

5. **Readiness scan: one new pattern and one new finding kind.** `_DATE_FORMATS` gains
   `("dotted", ^\d{4}\.\d{2}\.\d{2}$)`. Compact (`yyyymmdd`, validated as a real date) and both
   long forms are already recognised, so a mix of any of them with ISO already raises
   `mixed-date-formats`; tests pin that. `non-iso-dates` is added to `FINDING_KINDS`: it is emitted
   for a column whose per-format counts include any format other than `iso-date`/`iso-datetime`,
   with detail listing only the non-ISO formats and counts. It is advisory: an 8-digit identifier
   that is a real date in 1900-2100 or a deliberate legacy column will be flagged, and the
   audit skill text says to read it as a prompt to check, not as an error. Consumers enumerate
   `FINDING_KINDS`, so the report shape is unchanged. A column mixing ISO and non-ISO values
   gets both findings.

6. **pdata write-path warning is a pure check plus CLI output.** A new function in
   `lib/pdata/service.py` takes the project, record group and the `fields` mapping, reads field
   descriptions from `record_group_fields`, and returns warning strings; it never raises and
   writes nothing. The `add` and `update` handlers call it after a successful write and print the
   strings to stderr (update uses the record's group). It is a warning, not a validation error,
   because verbatim columns and legacy columns awaiting conversion legitimately hold other forms.
   Only TEXT fields are checked (integer `_at` fields hold epochs). Date-like is decided by name suffix `_at`/`_date` or the word "date" in the description,
   excluding names ending `_text`; the ISO-form test is a small set of anchored patterns kept next
   to the check. CSV importers are not covered; `readiness-scan` serves that case.

7. **`ccmsg read` rendering.** The `sent_at` line is rendered from the stored value with the
   existing timestamp parser; only the printed line changes.

8. **Version.** Minor bump to 3.12.0 (new managed section, new detection, no breaking change).
   `uv.lock` is regenerated and committed in the same commit as the bump.

## Risks / Trade-offs

- [Tests asserting the old `T...Z` text in doctor/cutover/clean-hook-sessions output] -> update in
  the same commit as the output change.
- [A downstream consumer parses the cutover log lines] -> none found in `src`; the log is a
  markdown list for people. Confirm by grep at implementation time.
- [Adding a section changes the user-level file for every installer on next sync] -> intended;
  the existing auto-sync applies it and `ccst doctor` reports `install:synced`.
- [`non-iso-dates` false positives on 8-digit identifiers or legacy columns] -> advisory wording
  in the audit skill; the finding gives counts and formats, not values.
- [Date-like heuristic misses or over-matches fields] -> the warning is non-blocking and the
  heuristic is documented in the spec; `_text` names are excluded.

## Migration Plan

1. Implement on a feature branch in a worktree; run the full check suite.
2. Open a PR, review, merge.
3. `uv tool install --reinstall ~/repos/claude-code-session-tools`; the next `ccst` command syncs
   the new section. Verify with `ccst doctor` and print the installed block.
4. Only after step 3 is verified, notify the config-sync project through `ccmsg`.
Rollback: `ccst claude-md uninstall --section date-format --apply` removes the section; the
display changes revert with the commit.

## Open Questions

None outstanding. The three earlier questions (field warning, uniformly non-ISO columns, `ccmsg
read` timestamp) were resolved as: include the warning, add the `non-iso-dates` kind, and render
the `ccmsg read` time as `yyyy-MM-dd HH:mm UTC`.
