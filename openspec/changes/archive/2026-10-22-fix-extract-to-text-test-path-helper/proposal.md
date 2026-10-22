## Why

Three `extract-to-text` skill tests fail on any machine whose PATH contains a dangling symlink that appears in two PATH directories (here `compose-bridge`, a Docker Desktop link under both `/usr/bin` and the `/bin` alias, pointing at a WSL mount that is not present). The test helper `_env_without` builds a directory of symlinks to everything on PATH and skips names already present using `Path.exists()`, which follows symlinks: a dangling link reports "does not exist", so the second PATH directory tries to create the same link and raises `FileExistsError`. The tests never reach the behaviour they check (`textutil`, `ocrmypdf` missing, text PDF not OCR'd). Because the default `pytest` run includes these tests, "all checks pass" cannot be claimed on affected machines.

## What Changes

- `_env_without` detects an already-linked name with `os.path.lexists`, so a dangling link counts as present and the first PATH directory still wins.
- Add a regression test for the helper with a dangling link in two PATH directories.
- Patch release (test code only, no interface change).

## Capabilities

### New Capabilities
- `skills/extract-to-text-tests`: the skill's PATH-hiding test helper is robust to dangling symlinks and duplicate PATH entries.

### Modified Capabilities

## Impact

- `src/cc_session_tools/skills/extract-to-text/tests/test_extract_to_text.py`.
- `pyproject.toml`, `uv.lock`, `CHANGELOG.md`: patch bump 4.0.1 to 4.0.2 (stacked on the open mypy PR, which introduces 4.0.1).
