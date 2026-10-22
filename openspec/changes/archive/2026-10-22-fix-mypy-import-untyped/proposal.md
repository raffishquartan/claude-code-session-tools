## Why

`mypy src` exits 1 with 10 `import-untyped` errors on `main` (and failed the same way before 4.0.0), so "all checks pass" can never be claimed for this repo and a real type error would hide among them. Every error is a missing stub package in the development environment, not a code defect: `pandas-stubs` is only in the `dev` extra, and `types-PyYAML`, `types-openpyxl`, `types-xlrd` and `pillow`'s inline types are only present if the dev group was synced. CI never runs mypy, so nothing keeps it clean.

The archived `pdata-uuid-record-ids` design.md (D6c) also still says the migration origin lives only in the vector table; the shipped code also stores it in the `pdata_migrated_from` table and the dump header carries `-- migrated_from_format=3`.

## What Changes

- Declare every stub package mypy needs (`pandas-stubs`, plus the existing `types-PyYAML`, `types-openpyxl`, `types-xlrd`) in the `dev` dependency group, so `uv sync` gives a mypy-clean environment.
- Run `mypy src` in CI so the check stays at exit 0.
- Amend the archived design.md D6c so it matches the shipped lineage storage.
- Patch release (no interface change).

## Capabilities

### New Capabilities
- `cli/type-check-clean`: `mypy src` exits 0 in a freshly synced development environment and is enforced in CI.

### Modified Capabilities

## Impact

- `pyproject.toml`, `uv.lock`: stub packages in the dev group; patch bump 4.0.0 to 4.0.1.
- `.github/workflows/ci.yml`: new mypy step.
- `openspec/changes/archive/2026-10-22-pdata-uuid-record-ids/design.md`: D6c wording only.
- `CHANGELOG.md`.
