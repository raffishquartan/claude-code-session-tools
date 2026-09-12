## Why

The deauppet project's original document-to-text pipeline used `python-docx`'s
`"\n".join(p.text for p in doc.paragraphs)`, which returns only top-level body paragraphs and
never descends into tables - a single 63-row table with no body paragraphs produced an empty
mirror recorded as `extraction_status: ok`, silently losing ~7,156 characters. The fix (now
proven in production for a month: `~/cc/claude/extract-to-text.py`, refreshed 2026-09-06) routes
`.docx`/`.doc`/`.rtf`/`.odt` through `pandoc -t markdown` instead (preserves tables), extracts
`.pptx`/`.pptm` directly via `python-pptx` (pandoc has no pptx reader), and treats empty output as
a failure status, never a silent success. This is a generic capability any `cc-session-tools`
user doing document-to-markdown extraction would want, not something specific to one project's
evidence archive.

Separately, a CCST-vs-CCCS skill inventory (this session, 2026-09-12) identified CCCS's
`extract-pptx` skill as generic (no personal content, a document-format utility) and misplaced in
the personal dotfiles repo - the user agreed to move it into CCST.

## What Changes

- New skill `extract-pptx`: ported from CCCS's `skills/extract-pptx/` (scripts + tests), with its
  personal copyright header removed and its "personal skill, machine-local" framing reworded to
  match how CCST actually installs skills (bundled + symlinked via `ccst skills install`). No
  functional change to the script itself.
- New skill `extract-to-text`: a generic multi-format document-to-markdown extractor generalizing
  the deauppet reference implementation - `.docx`/`.doc`/`.rtf`/`.odt` via pandoc (never
  `python-docx` `.paragraphs`), `.pptx`/`.pptm` by delegating to the ported `extract-pptx` script
  (one pptx extractor, not two), `.eml` via the stdlib `email` module, `.msg` via `extract-msg`,
  `.pdf` via `pdftotext -layout`, `.xlsx` via `openpyxl`, and a small set of plain-text passthrough
  extensions. Two rules carried over unchanged: sniff the real file signature rather than trusting
  the extension alone (a `.msg`/`.url` mislabeled `.docx` is a real bug class this catches), and
  empty extracted output is always a failure status (`empty-output-CHECK-THIS`), never a silent
  `ok`.
- `pyproject.toml`: both skills' `tests/` directories added to `testpaths`; `python-pptx` and
  `extract-msg` added to the `dev` dependency group (needed to build test fixtures / for test
  collection - `openpyxl` is already present transitively). External binaries (`pandoc`,
  `pdftotext`, `tesseract`, `soffice`) stay undeclared, manually-installed, and documented per
  format in each skill's own troubleshooting table, exactly like `extract-pptx` already does -
  none become a CCST runtime dependency, since imports stay lazy inside functions.

## Capabilities

### New Capabilities
- `skills/extract-to-text`: the multi-format document-to-markdown extraction behavior and its two
  hard rules (signature-sniffing, empty-output-is-failure).

### Modified Capabilities
(none)

## Impact

- `src/cc_session_tools/skills/extract-pptx/` (new, ported from CCCS).
- `src/cc_session_tools/skills/extract-to-text/` (new).
- `pyproject.toml`: `testpaths`, `dev` dependency group.
- `CHANGELOG.md`, version bump: minor (new skills, additive, no interface change to anything
  existing).
- Out of scope for this change (per `extract-to-text-notes.md`'s catalog of known pptx gaps):
  native Office charts, SmartArt, slide/presentation comments, hyperlink targets, and
  layout/master background text are not extracted - documented as known limitations, not silent
  gaps.
- Follow-up, not part of this change: after merging, send a message to the CCCS project noting
  `extract-pptx` is now in CCST and its CCCS copy should be removed (per the user's explicit
  instruction).
