## 1. Skill wording

- [x] 1.1 Rewrite "What to draft", "Confirmation step" and "Final output" in `src/cc_session_tools/skills/compact-smart/SKILL.md` so the paragraph is one line with no list markup; verify with a live `claude -p` run on a long synthetic session (4 runs, each produces exactly one fenced line)

## 2. Release

- [x] 2.1 Add a Fixed entry under a 3.16.2 heading in `CHANGELOG.md` and bump `pyproject.toml` to 3.16.2; run `uv lock` and verify `uv.lock` shows 3.16.2
- [x] 2.2 Run `uv run pytest -q` and verify it exits 0
