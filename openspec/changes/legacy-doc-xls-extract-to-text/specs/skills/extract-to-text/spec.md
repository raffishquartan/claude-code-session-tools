## RENAMED Requirements

- FROM: `### Requirement: Word-processor formats extract via pandoc, never `python-docx` paragraphs-only`
- TO: `### Requirement: Modern word-processor formats extract via pandoc, never `python-docx` paragraphs-only`

## MODIFIED Requirements

### Requirement: Modern word-processor formats extract via pandoc, never `python-docx` paragraphs-only
`.docx`, `.rtf`, and `.odt` files SHALL be extracted via `pandoc -t markdown`, which preserves
table content. The extractor SHALL NOT use `python-docx`'s `.paragraphs` (or any equivalent that
only walks top-level body paragraphs) as its extraction method for these formats. Legacy binary
`.doc` is not handled by pandoc; see the legacy `.doc` requirement.

#### Scenario: A table-only document extracts its table content
- **WHEN** a `.docx` file's only content is a table (no top-level body paragraphs)
- **THEN** the extracted markdown contains the table's cell content, not an empty body

### Requirement: The file's real signature is checked, not just its extension
Before routing a `.docx`/`.doc`/`.rtf`/`.odt`-extensioned file to its handler, the extractor SHALL
check the file's actual binary signature. A `.docx`/`.rtf`/`.odt`-extensioned file whose signature
indicates it is actually an Outlook `.msg` (OLE2/CFBF), or any `.docx`/`.doc`/`.rtf`/`.odt` file
that is a Windows Internet Shortcut, SHALL be routed to the correct handler (or reported as not
extractable content) rather than sent to pandoc. A `.xls`-extensioned file SHALL likewise be
checked for the ZIP signature of an OOXML workbook.

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

## ADDED Requirements

### Requirement: Legacy `.doc` extracts via `textutil`, with a `.msg` fallback
On a platform that provides `textutil` (macOS), a `.doc` file SHALL be extracted to plain text with
`textutil`. If `textutil` cannot read the file and the file begins with the OLE2/CFBF signature, the
extractor SHALL process it as an Outlook `.msg` file instead. If `textutil` fails and the file does
not have that signature, extraction SHALL fail with the `textutil` error.

#### Scenario: A real legacy `.doc` extracts
- **WHEN** a legacy binary `.doc` file is extracted on macOS
- **THEN** the extracted body contains the document's text

#### Scenario: An Outlook `.msg` named `.doc`
- **WHEN** a file named `something.doc` is really an Outlook `.msg` that `textutil` cannot read
- **THEN** the extractor processes it as a `.msg` file

### Requirement: Legacy `.doc` on a platform without `textutil` fails loudly
When `textutil` is not on `PATH`, extraction of a `.doc` file SHALL fail with a message stating that
legacy `.doc` extraction is only supported where `textutil` exists (macOS) and suggesting conversion
to `.docx` first. The extractor SHALL NOT skip the file silently or report a degraded result as
success.

#### Scenario: `.doc` extraction without `textutil`
- **WHEN** a `.doc` file is passed to the extractor and no `textutil` binary is on `PATH`
- **THEN** the extractor fails with a message naming `textutil` and macOS-only support, and the
  file's status is not `ok`

### Requirement: Legacy `.xls` extracts via `xlrd` in the `.xlsx` output shape
`.xls` files SHALL be extracted with `xlrd`, producing one `## Sheet: <name>` section per sheet
with each row rendered as `cell | cell | ...`, matching the `.xlsx` output shape. A file named
`.xls` that begins with the ZIP signature (an OOXML workbook) SHALL be extracted by the `.xlsx`
method instead.

#### Scenario: A legacy `.xls` extracts per sheet
- **WHEN** a legacy binary `.xls` workbook with two sheets is extracted
- **THEN** the output has a `## Sheet: <name>` section for each sheet containing its rows as
  `cell | cell | ...`

#### Scenario: An OOXML workbook named `.xls`
- **WHEN** a file named `something.xls` begins with the ZIP signature
- **THEN** it is extracted as an `.xlsx` workbook rather than failing in `xlrd`

#### Scenario: `.xls` extraction attempted without `xlrd`
- **WHEN** a legacy `.xls` file is passed to the extractor and `xlrd` is not installed
- **THEN** the extractor fails with a message naming `xlrd` and the command to install it

### Requirement: A PDF with no text layer is OCR'd with `ocrmypdf`
When `pdftotext` produces no non-whitespace text for a `.pdf` file, the extractor SHALL OCR the PDF
with `ocrmypdf` and use the recognised text as the body, recording OCR as the extraction method. A
PDF with a text layer SHALL NOT be OCR'd. If `ocrmypdf` is needed and not installed, extraction
SHALL fail with a message naming `ocrmypdf` and its install command.

#### Scenario: A scanned PDF extracts via OCR
- **WHEN** a `.pdf` with no text layer is extracted and `ocrmypdf` is available
- **THEN** the body is the OCR text and the extraction method names OCR

#### Scenario: A text PDF is not OCR'd
- **WHEN** a `.pdf` has a text layer
- **THEN** the body comes from `pdftotext` and `ocrmypdf` is not run

#### Scenario: A scanned PDF without `ocrmypdf`
- **WHEN** a `.pdf` with no text layer is extracted and `ocrmypdf` is not installed
- **THEN** the extractor fails with a message naming `ocrmypdf` and `brew install ocrmypdf`
