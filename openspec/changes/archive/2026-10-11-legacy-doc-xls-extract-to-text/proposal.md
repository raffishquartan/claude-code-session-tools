## Why

`extract-to-text` routes legacy binary `.doc` through pandoc, which cannot read it, and has no
`.xls` handling at all (openpyxl cannot read the legacy binary workbook format). A real evidence
archive of 42 `.doc` and 2 `.xls` files failed extraction. Legacy Office formats should be readable
in every project that uses the skill, not only via one project's private extractor.

## What Changes

- `.doc` moves off the pandoc row: it is extracted with macOS `textutil` (plain text, no tables).
  `.docx`/`.rtf`/`.odt` stay on pandoc.
- A `.doc` that `textutil` cannot read but whose head is the OLE2 magic is a mislabeled Outlook
  `.msg` and falls back to the existing `.msg` handler.
- On a platform without `textutil`, `.doc` fails loudly with a clear unsupported-platform message
  that names the problem and suggests converting to `.docx` first; no silent skip or degraded output.
- New `.xls` support via `xlrd`, rendered in the same shape as `.xlsx` (`## Sheet: <name>` per
  sheet, rows as `cell | cell | ...`). A file named `.xls` that is really an OOXML workbook (ZIP
  signature) is routed to the `.xlsx` path.
- A missing `xlrd` fails with a message naming the package and its install command.
- Scanned PDFs: when `pdftotext` yields no text, the extractor OCRs the PDF with `ocrmypdf` and
  records that method; a missing `ocrmypdf` fails with a message naming it and its install command.
- Empty output stays `empty-output-CHECK-THIS`.
- `SKILL.md`: description frontmatter, format table, signature-sniffing paragraph, troubleshooting
  table, and a dependency note covering `xlrd`, `python-pptx`, `textutil` (macOS only) and
  `ocrmypdf` (needed only for scanned PDFs; `tesseract` is its engine).
- `xlrd` added to the dev dependency group alongside `openpyxl`/`extract-msg`/`python-pptx`
  (which are likewise optional per-format dependencies, not runtime dependencies of the package).
- Tests: `.doc` via textutil (skipped off macOS), `.doc` that is really `.msg`, `.xls` via xlrd,
  `.xlsx`-content `.xls`, missing-`xlrd` message, scanned-PDF OCR (mocked `ocrmypdf`), missing-`ocrmypdf` message, `.doc` on a platform without `textutil`.
- CHANGELOG entry and minor version bump (3.14.0 to 3.15.0, backwards-compatible feature).

## Capabilities

### New Capabilities

### Modified Capabilities
- `skills/extract-to-text`: `.doc` no longer routes via pandoc; adds legacy `.doc` (textutil, `.msg`
  fallback, no-textutil failure) and `.xls` (xlrd, OOXML sniff) requirements; signature-check
  requirement extended to `.doc` and `.xls`; adds scanned-PDF OCR requirement.

## Impact

- `src/cc_session_tools/skills/extract-to-text/` (`SKILL.md`, `scripts/extract_to_text.py`, `tests/`)
- `pyproject.toml` (dev group, version), `uv.lock`, `CHANGELOG.md`
- No `ccst doctor` change: it has no per-format extraction dependency checks today.
- The `extract-pptx` skill is unchanged; `python-pptx` is already declared in the dev group.
