## Context

All 10 errors are `import-untyped` for `pandas`, `yaml`, `openpyxl`, `xlrd` and `PIL`; none is in project code. The venv already shows only the 6 pandas ones, so the cause is stub packages missing from the environment.

## Goals / Non-Goals

**Goals:**
- `uv sync && uv run mypy src` exits 0; CI keeps it so.

**Non-Goals:**
- No `ignore_missing_imports` overrides and no source changes: silencing the check would hide real gaps.
- No change to the existing `tiktoken_tools` and `pytesseract` overrides.

## Decisions

- Put the stubs in `[dependency-groups] dev` (what `uv sync` installs by default) rather than only the `dev` extra, so the one command works. Keep the extra as is for pip users.
- Add the CI step to the existing `test` job after the sync, so it runs on every OS and Python version and version-specific typing differences surface.
- If a stub set still leaves an error after syncing (for example `PIL` on an older Pillow), fix it at the dependency pin, not with an ignore.
