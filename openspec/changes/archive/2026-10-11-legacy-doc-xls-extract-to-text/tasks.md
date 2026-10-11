## 1. Extractor

- [x] 1.1 Remove `.doc` from the pandoc branch and add a `.doc` branch (`textutil`, OLE2 `.msg` fallback, no-`textutil` failure); verify with the new `.doc` tests
- [x] 1.2 Add `.xls` branch via `xlrd` with ZIP-signature routing to the `.xlsx` path and a missing-`xlrd` message; verify with the new `.xls` tests
- [x] 1.3 Update the module docstring's per-format dependency list; verify it names `xlrd` and `textutil`

- [x] 1.4 Add the scanned-PDF OCR fallback (`ocrmypdf --skip-text --sidecar` when `pdftotext` output is blank; loud missing-dependency error); verify with the new PDF tests

## 2. Tests

- [x] 2.1 Add tests: `.doc` via textutil (skip off macOS), `.doc` that is a `.msg`, `.doc` without textutil, `.xls` via xlrd, `.xlsx`-content `.xls`, missing `xlrd`, scanned-PDF OCR (mocked `ocrmypdf`), text PDF not OCR'd, missing `ocrmypdf`, empty output status; verify `uv run pytest src/cc_session_tools/skills/extract-to-text/tests -q` passes
- [x] 2.2 Add `xlrd` to the dev dependency group and run `uv lock`; verify `uv run mypy` and the full `uv run pytest -q` exit 0

## 3. Documentation and release

- [x] 3.1 Update `SKILL.md`: description frontmatter, format table, signature-sniffing paragraph, troubleshooting rows, a dependency note (`xlrd`, `python-pptx`, `textutil` macOS-only, `ocrmypdf` for scanned PDFs), and the `.doc` plain-text limitation; verify by reading it against the spec
- [x] 3.2 Add the CHANGELOG entry (Keep a Changelog) and bump `pyproject.toml` to 3.15.0 with the regenerated `uv.lock` in the same commit; verify `git diff` shows version and lock agree
