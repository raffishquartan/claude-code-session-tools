## Context

The audit skill's Phase 4 blocker list and Phase 2's header/schema-consistency bullet are largely
mechanical facts about CSV content. `lib/pdata/classify.py` reads only a CSV's header (with plain
`utf-8`, so a BOM ends up inside the first header name, the documented garbled-header failure).
`ccst pdata init`'s dry run consumes the audit's manifest, so the scan complements it. Probes of
68 real CSVs (aggregate counts only) informed the heuristics: 41 were CRLF, 9 had a BOM, 54% of
naive "unique column" hits were free text and 12 files' hits were `row_id`-style, the date
heuristic fired on 2, and 116 columns mix `,` with `;` versus 15 mixing `|` with `;`.

## Goals / Non-Goals

**Goals:**
- One deterministic command produces the mechanical Phase 4 findings (except the two blind spots
  below), the per-file inventory and unique-column facts, identically on every run.
- Agents get only their domain's slice and a judgement-only checklist; the skill is materially
  smaller with nothing it currently guarantees dropped.

**Non-Goals:**
- Multi-schema single files beyond the cheap `repeated-header` signal, and comma/semicolon list
  columns: comma noise in prose makes a deterministic rule unreliable, so both stay explicit agent
  bullets and are named as the scan's known blind spots.
- JSON, Markdown and other file types (only counted, by extension).
- Index-to-disk correspondence, controlled vocabularies, cross-references, OneDrive conflict
  detection (separate skill).
- Writing the manifest or proposing a schema: `ccst pdata init` and `pm-pdata-design-schema`.
- Measuring the token cost of a live audit.

## Decisions

- **New verb, new module `lib/pdata/readiness.py`** with pure functions returning frozen
  dataclasses; the CLI handler only resolves the project, calls the scanner and formats. Kinds are
  one `FINDING_KINDS` constant. Alternative: extend `classify.py`; rejected because classification
  proposes a `--write` manifest and must stay conservative.
- **Project resolution: `store.project_root(project)`**, which validates and joins without
  creating anything, then require the directory to exist. No new resolution logic.
- **Coverage equals migration coverage:** skip exactly `EXCLUDED_DIR_NAMES`, walk with
  `os.walk(followlinks=False)`, and test that the scanned set equals `walk_and_classify`'s CSVs, so
  a CSV in a dot-directory that `init` would import is never silently skipped.
- **Read each file once as bytes**, then derive `bom` (leading `EF BB BF`), `crlf` (`\r\n` present),
  and parse `raw.decode("utf-8-sig")` with `csv.reader(strict=True)`. Strict parsing turns an
  unterminated quote into an error instead of one huge record; `unreadable` reasons are the
  enumerated strings `empty-file`, `not-utf8`, `csv-parse-error`, `field-too-large`, never an
  exception message, so file content cannot reach the report by construction.
- **Findings carry counts and row numbers, never cell text.** Row numbers are 1-based data records,
  header excluded, and the report says that a quoted multiline cell makes them differ from line
  numbers.
- **Unique columns, not "natural keys".** A unique column is a fact; whether it is a stable key is
  judgement. Labels `integer-like` and `free-text` (mean length > 64) and a standing caveat keep the
  report from recommending the exact `row_id`-style keys the skill warns about. Requires at least
  two data rows so header-only and one-row files are not vacuously "perfect".
- **Date formats** by anchored regexes; `YYYYMMDD` must be a real date in 1900-2100 so numeric ids
  are ignored; a column is flagged only when it matches two or more formats.
- **Separators:** only `|` and `;`, flagged when both appear in values.
- **`--format markdown|json`** to match the other output-bearing `ccst pdata` verbs, `--path
  PREFIX` and `--findings-only` so the skill can hand an agent its slice. Default Markdown omits
  full header lists (column counts only); JSON carries them.
- **Exit 0 whenever the scan ran**; a non-zero exit on findings would make an unattended caller
  treat a messy project as a tool failure.
- **Skill rewrite:** first step runs the scan; Phase 1 keeps the conflict cleanup but replaces the
  external scanner's false-positive prose with a pointer; Phase 2 keeps the domain fan-out but only
  above three domains, with a judgement-only checklist; Phase 4 becomes "fold the scan's findings
  in, add what it cannot see"; Phases 3 and 5 keep their structure (Phase 5's six manifest
  sections are consumed by `pm-pdata-do-init`), tightened in wording. The rewrite fixes the
  "Four phases" heading over five phases, and states in one line which of the four "readiness"
  artefacts (the scan, `pdata-readiness-notes.md`, the Phase 5 manifest, and init's
  `.pdata-migration-manifest.json`) is which.
- **No permanent byte-size assertion.** A test asserts the behaviour (scan-first ordering, kinds
  named, domain threshold, row_id warning, delete-script rule, the six manifest sections and
  checkpoint-table split); the before/after size is measured once and recorded in the changelog.
- **Changelog convention:** a dated `## [3.8.0]` section above 3.7.1, as the previous release did.
- **Minor version (3.8.0):** additive subcommand, per this repo's version policy.

## Risks / Trade-offs

- [Heuristic false positives, e.g. a `-` cell that is a legitimate dash] -> kinds and counts only;
  the skill says findings are leads to verify.
- [Scan report costs agent tokens] -> compact default, `--path` slices per agent, `--findings-only`.
- [Scanning a cloud-synced folder opens every CSV, which can force cloud-only placeholders to
  download] -> stated here; the scan reads each file once and never writes.
- [Skill and scan drift] -> a test asserts the skill mentions every entry of `FINDING_KINDS`.
- [Skill rewrite drops guidance dependents rely on] -> tests assert the row_id warning and the six
  manifest sections survive, and a task re-checks `pm-pdata-do-manual-migration`'s cross-reference.
- [Live-audit savings unmeasured] -> the changelog claims only fewer, exactly-derived facts and
  the measured skill size.
