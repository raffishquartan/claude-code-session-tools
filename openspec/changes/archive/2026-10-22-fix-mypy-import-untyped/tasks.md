## 1. Stubs and CI

- [x] 1.1 Add `pandas-stubs` to `[dependency-groups] dev` in `pyproject.toml`; run `uv lock` and `uv sync`; verify `uv run mypy src` exits 0
- [x] 1.2 Add a `Type check` step (`uv run mypy src`) to the `test` job in `.github/workflows/ci.yml`

## 2. Archived design wording

- [x] 2.1 Amend D6c in the archived `2026-10-22-pdata-uuid-record-ids/design.md` so the origin is described as stored in `pdata_migration_origin` and `pdata_migrated_from`, with the dump header carrying `-- migrated_from_format=3`; check the wording against `src/cc_session_tools/lib/pdata/migrate_uuid.py` and `dump.py`

## 3. Release

- [x] 3.1 Add a 4.0.1 Fixed entry to `CHANGELOG.md`, bump `pyproject.toml` to 4.0.1, run `uv lock`, verify `uv.lock` shows 4.0.1
- [x] 3.2 Run `uv run pytest -q` and `uv run mypy src`; verify both exit 0
