---
name: extract-pptx
description: Use when text needs to be extracted from a .pptx PowerPoint file - slide text, tables, speaker notes, text baked into images (OCR), or embedded file objects (embedded workbooks/documents). Also use when a .pptx needs a sibling print-to-PDF. Triggers on "extract this pptx", "read this powerpoint", "pptx won't extract", "pandoc can't read pptx", empty/failed pptx mirrors in a text-extraction pipeline, or any pptx-to-markdown/pptx-to-pdf task. Do NOT use pandoc for pptx input - pandoc has a markdown writer but no pptx reader.
---

# Extract PPTX

## Why this exists

Pandoc has a markdown *writer* but no `.pptx` *input reader*. Any pipeline that
routes Office formats through `pandoc -t markdown` (a correct, common pattern
for preserving Word/RTF tables) silently or loudly fails on every `.pptx` file.
There is no pandoc flag that fixes this - it needs a different extractor
entirely. This skill wraps a `python-pptx`-based one. The sibling `extract-to-text`
skill delegates its own `.pptx`/`.pptm` handling to this script rather than
re-implementing pptx extraction.

## When to use

- A `.pptx` file needs its text extracted and pandoc has failed, returned empty
  output, or isn't an option.
- A text-extraction pipeline's `--check`/audit step shows `.pptx` mirrors as
  empty, missing, or `FAILED`.
- Text is baked into an image on a slide (a chart export, a scanned page
  pasted as a picture) and needs OCR to be readable.
- A slide has an embedded object (an embedded Excel sheet, a Word doc) whose
  content needs to be pulled out, not just referenced.
- A clean, paginated PDF render of a `.pptx` is needed alongside the text
  extraction (evidence packs, archival records).

**Do NOT use** `pandoc -t markdown <file>.pptx` - it will fail or silently
produce nothing useful. Do not hand-roll a second one-off pptx extractor
either; use or extend this one so behaviour stays consistent across projects.

## How it works

`scripts/extract_pptx.py` opens the file with `python-pptx` and, per slide,
in order:

1. Walks every shape (text frames, tables, and groups recursively) and
   collects text in slide order. Table cells are extracted cell-by-cell, not
   just shape text - this matters the same way it matters for docx tables
   (see the sibling `extract-to-text` skill's docx-table-loss motivation).
2. If OCR is enabled (default), runs Tesseract over every picture shape's
   image bytes and appends the result labelled as OCR text, distinct from
   slide body text.
3. Extracts speaker notes if present, labelled distinctly from slide body
   text.
4. Extracts embedded OLE/package objects (embedded workbooks, documents)
   referenced by the slide's relationships, if `--embeds-dir` is given.
5. Optionally renders a sibling PDF via headless LibreOffice (`soffice`), if
   `--pdf-out` is given.

## Procedure

```bash
python3 ~/.claude/skills/extract-pptx/scripts/extract_pptx.py <source.pptx> \
    --out-md <output.md> \
    [--embeds-dir <dir-for-embedded-files>] \
    [--pdf-out <output.pdf>] \
    [--no-ocr]
```

- `--out-md` is required; the other three are opt-in.
- OCR is **on by default** and fails loudly (non-zero exit, clear message) if
  `pytesseract`/Pillow or the `tesseract` binary is missing - it does not
  silently skip OCR and produce an incomplete-looking-complete file. Pass
  `--no-ocr` explicitly if OCR isn't wanted for this run.
- `--embeds-dir` is only written to if the slide actually has embedded
  objects; it's never created empty.
- `--pdf-out` requires `soffice` (LibreOffice) on `PATH`. If it's missing the
  script exits non-zero with a clear message rather than skipping the PDF
  silently - report that to the user rather than treating the run as
  complete.

Verify the output: open `--out-md` and confirm slide count and non-trivial
content; a `.pptx` with no extractable text on any slide is possible
(image-only decks) but should be rare - treat an entirely-empty result the
same way a hand-rolled pipeline treats `empty-output-CHECK-THIS`, not as a
quiet success.

## Refusing fallbacks

Do not silently drop OCR, embeds, or the PDF render when a dependency is
missing and substitute a partial result without saying so. The script is
built to fail loudly for exactly this reason - surface the failure to the
user rather than working around it.

## Reusing this in a project-local extractor

If a project has its own canonical text-extraction script (e.g. one that
mirrors an archive into markdown files with status metadata), do not import
this script into it directly across a network/OneDrive boundary - a
project's canonical script needs to run standalone on every machine it syncs
to, independent of whether this bundled skill happens to be installed there.
Port the relevant logic (shape/table/notes walking at minimum) into the
project's own script instead, matching that script's existing conventions for
status/output. Treat OCR, embeds, and PDF rendering as separate, explicitly
opted-into capabilities when porting - they add real dependencies and runtime
cost that a project's canonical extractor may not want by default.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `pandoc failed` / empty pandoc output on a `.pptx` | pandoc has no pptx reader | Don't use pandoc for pptx; use this skill instead |
| `ModuleNotFoundError: pytesseract` | OCR deps not installed | `pip install --user pytesseract pillow` (add `--break-system-packages` if the environment is externally-managed) |
| `tesseract binary is not on PATH` | Tesseract OCR engine not installed | Install `tesseract-ocr` (apt/brew/winget) |
| `no soffice/libreoffice binary found` | LibreOffice not installed | Install LibreOffice, or run in a container that has it |
| OCR text is garbled/partial | Low-res or stylised image text | Expected - Tesseract accuracy depends on image quality; note it as approximate in any evidence use |
| Embeds dir stays empty | Slide genuinely has no embedded OLE/package objects | Not a bug - only shapes with an actual embed relationship produce output |
