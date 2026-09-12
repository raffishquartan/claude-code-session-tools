## 1. Wrapping helper (TDD)

- [x] 1.1 Add a `_wrap_reason(reason: str, *, indent: str = "    ") -> str` helper to
      `src/cc_session_tools/lib/doctor.py` using `shutil.get_terminal_size(fallback=(80, 24)).columns`
      and `textwrap.fill(reason, width=..., initial_indent=indent, subsequent_indent=indent)`.
      Write failing tests first covering: wraps at a monkeypatched narrow width, wraps at a
      monkeypatched wide width, and falls back to 80 columns when `shutil.get_terminal_size` raises
      or reports no real terminal (non-tty). Verify: new tests pass (`uv run pytest -q
      tests/test_ccst_doctor.py -k wrap_reason`).

## 2. Wire into `format_results`

- [x] 2.1 Update `format_results()` to render each non-OK result as `[STATUS] name` on its own
      line followed by `_wrap_reason(r.reason)` on the next line(s); leave the `--all` OK-line
      rows in their existing single-line `[OK]   name  reason` format. Verify: existing
      `test_format_results_*` tests updated for the new two-line shape and passing.
- [x] 2.2 Add a test asserting two consecutive non-OK results produce distinguishable
      `[STATUS] name` header lines (regression test for the original "ragged wrap looks like a new
      result" problem). Verify: test passes.

## 3. Wire into `format_drift_report`

- [x] 3.1 Update `format_drift_report()` to use the same two-line `[STATUS] name` +
      `_wrap_reason(reason)` layout as `format_results()`. Verify: existing
      `format_drift_report` tests updated and passing.

## 4. Full verification and release

- [x] 4.1 Run `uv run pytest -q` (full suite) and `uv run mypy` (or the project's configured mypy
      invocation) and confirm both are clean.
- [x] 4.2 Manually run `uv run python -m cc_session_tools.cli.ccst doctor` (with
      `CCST_NO_AUTO_SYNC=1`) at a couple of `COLUMNS` values (e.g. `COLUMNS=80` and `COLUMNS=200`)
      against the real `~/.claude` state and visually confirm the new layout reads cleanly at
      both widths.
- [x] 4.3 Update `CHANGELOG.md` with a `### Fixed` entry describing the readability fix, and bump
      `pyproject.toml`'s version as a patch release; run `uv lock` and commit the regenerated
      `uv.lock` in the same commit.
