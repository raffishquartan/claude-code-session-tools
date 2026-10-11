## Context

`extract_to_text.py` has one branch per extension group; `.docx/.doc/.rtf/.odt` share a pandoc branch
guarded by OLE/shortcut sniffing, and `.xlsx` uses `openpyxl` in read-only mode. Per-format Python
packages (`openpyxl`, `extract-msg`, `python-pptx`) are optional: imported lazily with an install
hint on failure, and declared only in the dev dependency group. See proposal.md - Why.
A private extractor in another project already proves the approach; its logic is re-implemented here,
not copied, so no `master_copy:` provenance is needed.

## Goals / Non-Goals

**Goals:**
- Read legacy `.doc` and `.xls` through the same CLI, status and frontmatter contract.
- Fail loudly (never silently) when a platform tool or package is missing.

**Non-Goals:**
- `.doc` table preservation (`textutil` emits plain text).
- A non-macOS `.doc` converter (antiword/LibreOffice).
- OCR of image-only inputs other than PDFs.
- Legacy `.ppt`.

## Decisions

- **`.doc` via `textutil -convert txt -stdout`.** Built in on macOS, no install. Alternatives:
  LibreOffice (heavy, slow startup, not installed) and antiword (unmaintained, extra install).
- **Non-macOS `.doc` fails with a clear message** suggesting conversion to `.docx`, rather than
  guessing at an optional converter. Revisit if a non-macOS user needs it.
- **`.msg` fallback only after `textutil` fails and the head is OLE2.** Real `.doc` files are also
  OLE2, so the magic alone cannot identify a `.msg`; trying `textutil` first avoids misrouting.
- **`.xls` via `xlrd` with `on_demand=True`**, rendering shared with the `.xlsx` code path where
  practical so the two output shapes cannot drift. ZIP-signature sniff routes OOXML-named-`.xls`
  to openpyxl via a file object, because openpyxl rejects the `.xls` extension by name.
- **`xlrd` added to the dev dependency group**, consistent with `openpyxl`/`extract-msg`; it is not
  a runtime dependency of the package.
- **Scanned PDFs: OCR fallback via `ocrmypdf --skip-text --sidecar`.** Only when `pdftotext` returns
  whitespace-only output, so text PDFs never pay the OCR cost. The sidecar text file is the result
  and the OCR'd PDF is discarded in a temp dir. `ocrmypdf` is an on-demand dependency (like
  `xlrd`): checked only when OCR is needed, failing loudly with an install hint
  (`brew install ocrmypdf`; `tesseract` is its engine). Alternative considered: always OCR (slow,
  alters good text) or a `--ocr` flag (`--ocr` already means pptx image OCR; reusing it would blur
  two meanings).
- **`python-pptx` stays declared via the dev group** (used by `extract-pptx`); SKILL.md lists it.
- **`ccst doctor` unchanged:** it has no per-format extraction dependency checks to extend.

## Risks / Trade-offs

- [Plain text only for `.doc`, tables flattened] → documented in SKILL.md known limitations.
- [`textutil` unavailable on Linux/Windows] → explicit failure message, documented as macOS-only.
- [A blank PDF now triggers OCR and then reports `empty-output-CHECK-THIS`, or fails if `ocrmypdf` is missing] → acceptable: both are non-`ok` statuses.
- [Tests relying on a real legacy `.doc` fixture] → skip off macOS; the `.msg`-named-`.doc` and
  no-`textutil` cases are testable everywhere by mocking `PATH`/`which`.
