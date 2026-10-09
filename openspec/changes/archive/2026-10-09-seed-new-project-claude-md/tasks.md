## 1. Seed text

- [x] 1.1 Add failing tests for `seed_text(project)`: heading, both section headers, the three date clauses and exemptions, the four line-ending rules, LF only, no home-directory path and no absolute path
- [x] 1.2 Add `src/cc_session_tools/lib/pdata/project_claude_md.py` with the text constants and `seed_text`

## 2. Scaffolding

- [x] 2.1 Add failing tests in the init-paths and init-service tests: fresh project (dry-run and `--write`) gets `CLAUDE.md`; existing project without one gets none; existing project's `CLAUDE.md` bytes unchanged; edit kept and deletion not undone on a second run; rehearsal target untouched
- [x] 2.2 Rename `scaffold_new_project_dirs` to `scaffold_new_project(project_root, project)`, write the seed when `CLAUDE.md` is absent, and update the call in `init_service.dry_run`

## 3. Release

- [x] 3.1 Run the full check suite (`uv run pytest -q`, `uv run mypy src`) and confirm exit 0
- [x] 3.2 Update `CHANGELOG.md` and bump `pyproject.toml` to the next minor version above 3.13.0; run `uv lock` and commit `uv.lock` in the same commit
- [x] 3.3 Sync the delta spec into `openspec/specs/` and archive the change before shipping
- [ ] 3.4 Push the branch, open the PR against `main`, run `do-code-review` and post its findings as a PR comment; report the PR URL and stop (do not merge)
