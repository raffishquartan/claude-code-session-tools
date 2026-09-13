## Purpose

Defines the behavior of the `extract-to-text` skill: converting a document to markdown text
without silently losing content that a naive extractor would miss (tables in Office documents
being the motivating case), and never reporting a broken extraction as a success.

## ADDED Requirements

### Requirement: Word-processor formats extract via pandoc, never `python-docx` paragraphs-only
`.docx`, `.doc`, `.rtf`, and `.odt` files SHALL be extracted via `pandoc -t markdown`, which
preserves table content. The extractor SHALL NOT use `python-docx`'s `.paragraphs` (or any
equivalent that only walks top-level body paragraphs) as its extraction method for these formats.

#### Scenario: A table-only document extracts its table content
- **WHEN** a `.docx` file's only content is a table (no top-level body paragraphs)
- **THEN** the extracted markdown contains the table's cell content, not an empty body

### Requirement: `.pptx`/`.pptm` extract via `python-pptx`, not pandoc
`.pptx` and `.pptm` files SHALL be extracted via `python-pptx` (slide text, table cells, and
speaker notes, in slide order), since pandoc has no `.pptx` input reader and would fail or
produce no output for these formats.

#### Scenario: A pptx with a table extracts its table content
- **WHEN** a `.pptx` file has a slide containing a table
- **THEN** the extracted markdown contains that table's cell content

### Requirement: The file's real signature is checked, not just its extension
Before routing a `.docx`/`.doc`/`.rtf`/`.odt`-extensioned file to pandoc, the extractor SHALL
check the file's actual binary signature. A file whose signature indicates it is actually an
Outlook `.msg` (OLE2/CFBF) or a Windows Internet Shortcut, despite its extension, SHALL be routed
to the correct handler (or reported as not extractable content) rather than sent to pandoc.

#### Scenario: A `.msg` file mislabeled with a `.docx` extension
- **WHEN** a file named `something.docx` has the OLE2/CFBF binary signature of a real Outlook
  `.msg` file
- **THEN** the extractor processes it as a `.msg` file rather than failing with an opaque pandoc
  container error

#### Scenario: A Windows Internet Shortcut mislabeled with a `.docx` extension
- **WHEN** a file named `something.docx` has the `[InternetShortcut]` signature of a Windows
  `.url` shortcut
- **THEN** the extractor reports that the file is a shortcut, naming its link target, rather than
  attempting document extraction on it

### Requirement: Empty extracted output is always a failure status, never a silent success
When extraction produces no non-whitespace body content, the extractor SHALL report a distinct
failure status (not the same status used for a genuine successful extraction), so an empty
result is never indistinguishable from a successfully extracted, genuinely-empty document.

#### Scenario: An extraction that legitimately produces nothing is flagged, not hidden
- **WHEN** extraction of a given file completes without raising an error but produces only
  whitespace as its body
- **THEN** the extractor's reported status differs from its success status, calling out the empty
  result for a human to check

### Requirement: Each supported format's optional external dependency is documented and fails loudly when missing
For a format whose extraction requires an external binary or a non-core Python package not
installed by default, the skill's documentation SHALL name the exact dependency and install
command, and the extractor SHALL fail with a clear, actionable message identifying the missing
dependency rather than silently skipping that file or falling back to a degraded result.

#### Scenario: A pdf extraction attempted without `pdftotext` on PATH
- **WHEN** a `.pdf` file is passed to the extractor and no `pdftotext` binary is on `PATH`
- **THEN** the extractor exits with a clear message naming `pdftotext` as the missing dependency
