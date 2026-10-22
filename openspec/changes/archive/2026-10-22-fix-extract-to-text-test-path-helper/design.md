## Context

`os.listdir` lists dangling links; `Path.exists()` is false for them, so the dedupe check misses them. Reproduced with `uv run pytest src/cc_session_tools/skills/extract-to-text/tests`: 3 failures, `FileExistsError` for `compose-bridge`.

## Goals / Non-Goals

**Goals:**
- The three tests run their real assertions on machines with dangling PATH links.

**Non-Goals:**
- No change to the skill's production code, and no skipping of the tests.

## Decisions

- Use `os.path.lexists` instead of `Path.exists()` for the dedupe check; first PATH directory wins, as before.
- Put the regression test beside the helper in the same test module.
