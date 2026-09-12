---
name: extract-to-text
description: Use when a document needs to be converted to markdown text - .docx/.doc/.rtf/.odt, .pptx/.pptm, .eml/.msg, .pdf, .xlsx, or plain text. Triggers on "extract this document", "convert to markdown", "read this file's text", empty/failed extraction mirrors in a text-extraction pipeline, or any document-to-markdown task. Do NOT use python-docx's `.paragraphs` for Word documents - it never descends into tables and can silently produce an empty result for a table-only document. Do NOT use pandoc for .pptx - pandoc has no pptx reader; this skill delegates that case to the extract-pptx skill.
---

# Extract to text

## Why this exists

An earlier document-extraction pipeline used `python-docx`'s
`"\n".join(p.text for p in doc.paragraphs)`. That only returns top-level body
paragraphs and never descends into tables - a document whose only content is a
table produced an empty result recorded as a clean success. This skill exists
so that mistake doesn't get repeated: it routes each format through the tool
that actually preserves its structure, and treats an empty result as a failure
to investigate, never a quiet success.

## When to use

- A document needs converting to markdown and you're about to reach for
  `python-docx`, a hand-rolled `.pptx` reader, or a raw-text dump of an
  email - use this skill instead.
- A text-extraction pipeline's audit step shows empty, missing, or `FAILED`
  mirrors for any of the formats below.
- A file's extension and its actual content disagree (a mail-export tool
  named a `.msg` or a Windows shortcut with a `.docx` extension) - this skill
  checks the real file signature before routing, and doesn't just trust the
  extension.

## How it works

`scripts/extract_to_text.py <file>... --out-dir <dir>` writes one
`<filename>.md` per input into `--out-dir`, with a frontmatter block
(`source_path`, `ext`, `extraction_method`, `extraction_status`,
`extracted_at`) followed by the extracted body.

| Format | Method | Notes |
|---|---|---|
| `.docx` / `.doc` / `.rtf` / `.odt` | `pandoc -t markdown` | Preserves tables - never `python-docx .paragraphs`. Requires `pandoc` on `PATH`. |
| `.pptx` / `.pptm` | Delegates to the `extract-pptx` skill's script | Pandoc has no pptx reader. Pass `--ocr` to also OCR image text (off by default here; see `extract-pptx`'s own SKILL.md for OCR/embeds/PDF-render options beyond this skill's scope). |
| `.eml` | stdlib `email` module | No extra dependency. `text/html`-only messages are stripped to text with stdlib `html.parser`. |
| `.msg` | `extract-msg` package | Outlook's binary format has no stdlib reader. `pip install --user extract-msg`. |
| `.pdf` | `pdftotext -layout` | Requires the `poppler` package's `pdftotext` binary on `PATH`. |
| `.xlsx` | `openpyxl` | `pip install --user openpyxl`. One `## Sheet: <name>` section per worksheet, rows rendered as `cell | cell | ...`. |
| `.txt` / `.md` / `.html` / `.htm` / `.csv` / `.json` | Raw passthrough | Read as text with a UTF-8 / UTF-16 / Latin-1 fallback ladder. |

**Signature sniffing:** before routing a `.docx`/`.doc`/`.rtf`/`.odt`-extensioned file to
pandoc, the actual file signature is checked. A real Outlook `.msg` (OLE2/CFBF
signature) or a Windows Internet Shortcut (`[InternetShortcut]` signature)
mislabeled with one of those extensions is routed correctly (or reported as a
shortcut, naming its link target) instead of failing with an opaque pandoc
container error.

**Empty output is never `ok`.** If extraction completes without error but
produces only whitespace, the status is `empty-output-CHECK-THIS`, not `ok` -
an empty mirror must never be indistinguishable from a genuinely empty
document.

## Procedure

```bash
python3 ~/.claude/skills/extract-to-text/scripts/extract_to_text.py <file> [<file> ...] \
    --out-dir <output-dir> \
    [--ocr]
```

Verify the output: open the written `.md` file(s) and confirm
`extraction_status: ok` with non-trivial body content. Any status other than
`ok` names the problem in the frontmatter and in a warning banner at the top
of the file - investigate the source directly rather than trusting the mirror.

## Refusing fallbacks

Do not silently substitute a partial result when a format's dependency is
missing - each `extract()` branch fails with a message naming the exact
missing binary or package and how to install it (see the table above),
matching `extract-pptx`'s "fail loudly" design.

## Known limitations

Not extracted by any format above - documented gaps, not silent ones (pptx
specifics tracked in more detail by the sibling `extract-pptx` skill):

- Native Office charts and SmartArt (stored outside the text/table object
  model these tools expose).
- Slide/presentation comments.
- Hyperlink targets (only a run's display text is extracted, not the URL).
- Text baked into a slide layout/master background (letterhead, watermark).
- Legacy binary `.ppt` (pre-2007 OLE2 format, not the OOXML zip `python-pptx`
  reads) and password-protected/encrypted files of any format.

## Reusing this in a project-local extractor

If a project has its own canonical text-extraction script (e.g. one that
mirrors an archive into markdown files with status metadata alongside other
project-specific bookkeeping), port the relevant per-format logic into that
script rather than shelling out to this skill across a sync boundary - a
project's canonical script needs to run standalone on every machine it syncs
to, independent of whether this skill happens to be installed there. Keep the
two rules (pandoc over `python-docx` paragraphs; empty output is a failure)
regardless of how the rest is adapted.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `requires pandoc, which is not on PATH` | pandoc not installed | `brew install pandoc` / `winget install --id JohnMacFarlane.Pandoc` |
| `requires pdftotext (poppler)` | poppler not installed | `brew install poppler` / `winget install --id oschwartz10612.Poppler` |
| `requires the openpyxl package` | openpyxl not installed | `pip install --user openpyxl` |
| `extract-msg package is not installed` | extract-msg not installed | `pip install --user extract-msg` |
| `extract-pptx skill script not found` | extract-pptx skill not installed alongside this one | Install the `extract-pptx` skill too - this skill delegates pptx handling to it |
| `extraction_status: empty-output-CHECK-THIS` | Genuinely empty source, or an extractor silently returned nothing | Open the source directly and confirm before trusting the mirror |
