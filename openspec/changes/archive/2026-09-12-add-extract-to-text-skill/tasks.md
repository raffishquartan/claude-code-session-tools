## 1. Port `extract-pptx` from CCCS

- [x] 1.1 Copy `scripts/extract_pptx.py` and `tests/test_extract_pptx.py` from
      `/Users/cfog/repos/claude-code-config-sync/skills/extract-pptx/` into
      `src/cc_session_tools/skills/extract-pptx/{scripts,tests}/`, stripping the
      "Copyright (c) 2026 Chris Fogelberg..." header from both files (no other CCST skill carries
      one). Verify: `grep -rn Chris src/cc_session_tools/skills/extract-pptx/` returns nothing.
- [x] 1.2 Copy and reword `SKILL.md`: strip the copyright header block; reword the "Reusing this
      in a project-local extractor" section's "personal skills under `~/.claude/skills/` are
      machine-local" framing to match CCST's actual bundled-and-symlinked install model. Verify:
      `grep -n Chris src/cc_session_tools/skills/extract-pptx/SKILL.md` returns nothing.
- [x] 1.3 Add `src/cc_session_tools/skills/extract-pptx/tests` to `pyproject.toml`'s `testpaths`,
      and add `python-pptx` and `extract-msg` to the `dev` dependency group (both needed for test
      fixtures across this change - `extract-msg` is for the sibling `extract-to-text` skill's
      `.msg` tests, added here since it's the same pyproject.toml edit). Run `uv sync --extra dev`.
      Verify: `uv run pytest -q src/cc_session_tools/skills/extract-pptx/tests` passes.

## 2. Build the `extract-to-text` skill (TDD)

- [x] 2.1 Write failing tests for a `.docx` with only a table (no body paragraphs) extracting its
      table content via `pandoc -t markdown`, and for `.doc`/`.rtf`/`.odt` routing to the same
      pandoc path. Then implement `scripts/extract_to_text.py`'s pandoc-family dispatch. Verify:
      new tests pass.
- [x] 2.2 Write failing tests for `.pptx`/`.pptm` delegating to the ported `extract_pptx.py`
      script (not a second pptx implementation) and producing its table content in the output.
      Then implement the delegation. Verify: new tests pass.
- [x] 2.3 Write failing tests for `.eml` (stdlib `email` module, `text/plain` preferred,
      `text/html` fallback via stdlib `html.parser`) and `.msg` (`extract-msg` package), both
      rendering to the same markdown shape (header block + body + attachment list). Then
      implement. Verify: new tests pass.
- [x] 2.4 Write failing tests for `.pdf` (`pdftotext -layout`, skipped if not on `PATH`) and
      `.xlsx` (`openpyxl`, sheet name headers + row content). Then implement. Verify: new tests
      pass (pdf test uses `unittest.skipUnless(shutil.which("pdftotext"), ...)` matching the
      ported skill's own `HAS_SOFFICE`/`HAS_TESSERACT` convention for optional external tools).
- [x] 2.5 Write a failing test for the signature-sniffing rule: a `.msg`-signature file with a
      `.docx` extension routes to the `.msg` handler instead of pandoc; a `[InternetShortcut]`
      file with a `.docx` extension reports a clear "this is a shortcut" message instead of a
      pandoc container error. Then implement the signature check ahead of the pandoc dispatch.
      Verify: new tests pass.
- [x] 2.6 Write a failing test asserting that an extraction producing only whitespace body content
      reports a distinct non-`ok` status (e.g. `empty-output-CHECK-THIS`) rather than `ok`. Then
      implement this check as a post-extraction gate applied uniformly across every format.
      Verify: new test passes.
- [x] 2.7 Write `SKILL.md` for `extract-to-text`: when to use it, the CLI shape
      (`extract_to_text.py <file>... --out-dir <dir>`), the two hard rules stated explicitly, a
      per-format dependency table (pandoc/pdftotext binaries; python-pptx/extract-msg/openpyxl
      packages) matching `extract-pptx`'s troubleshooting-table style, and the known-limitations
      list from `extract-to-text-notes.md` (native charts, SmartArt, comments, hyperlink targets,
      layout/master background text) stated as documented gaps, not silent ones.

## 3. Full verification and release

- [x] 3.1 Run `uv run pytest -q` (full suite) and `uv run mypy` on both new scripts; confirm both
      are clean.
- [x] 3.2 Update `CHANGELOG.md` with an `### Added` entry for both skills, and bump
      `pyproject.toml`'s version as a minor release; run `uv lock` and commit the regenerated
      `uv.lock` in the same commit.
- [ ] 3.3 After merging, send a message to the `claude-code-config-sync` project noting
      `extract-pptx` is now in CCST and its CCCS copy (`skills/extract-pptx/`) should be removed.
