## 1. Externalize the gated-tool list (TDD)

- [x] 1.1 In `tests/test_confirm_8digit.py`, replace the imported `GATED_TOOLS_DEFAULT` with a
      test-local `_TEST_GATED_TOOLS` constant holding the same six tool names, and update every
      call site. Verify: file still imports cleanly and existing tests still describe the same
      behavior (they'll fail until step 1.2 lands `_gated_tools_from_env`, if `GATED_TOOLS_DEFAULT`
      is removed from the production module first - sequence 1.1 before 1.2's removal so this is a
      pure rename with no red window).
- [x] 1.2 Add a `_gated_tools_from_env()` helper to `src/hooks/confirm_8digit.py` reading
      `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` (comma-separated, trimmed, empty entries dropped, defaults
      to `[]` when unset). Write failing tests first: unset -> `[]`; multiple names -> parsed list;
      an empty entry between two names -> dropped, not kept as `""`. Verify: new tests pass.
- [x] 1.3 Remove the hardcoded `GATED_TOOLS_DEFAULT` module constant from
      `src/hooks/confirm_8digit.py` and update `main()` to call `_gated_tools_from_env()` instead.
      Update the module docstring to document the new env var, following the same "never
      hardcoded, because this module ships in a public repo" phrasing already used for
      `NOTIFY_EMAIL`. Verify: `uv run pytest -q tests/test_confirm_8digit.py` passes.
- [x] 1.4 Add a test exercising `main()` end-to-end (stdin JSON in, exit code out) confirming a
      gated-tool-shaped call is allowed unconditionally when `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` is
      unset, and is subject to the normal verification flow when it is set to include that tool
      name. Verify: test passes.

## 2. Reword the two personally-identifying skills

- [x] 2.1 Reword `src/cc_session_tools/skills/pm-pdata-do-init/SKILL.md` to remove all "Chris"
      references and the "WSL2 laptop and a MacBook" description, replacing with generic
      second-person phrasing ("you", "the user") with no change in instructional meaning. Verify:
      `grep -n Chris src/cc_session_tools/skills/pm-pdata-do-init/SKILL.md` returns nothing.
- [x] 2.2 Reword `src/cc_session_tools/skills/pm-pdata-resolve-conflicts/SKILL.md` the same way.
      Verify: `grep -n Chris src/cc_session_tools/skills/pm-pdata-resolve-conflicts/SKILL.md`
      returns nothing.

## 3. Full verification and release

- [x] 3.1 Run `uv run pytest -q` (full suite) and `uv run mypy src/hooks/confirm_8digit.py` and
      confirm both are clean.
- [x] 3.2 Update `CHANGELOG.md` with a `### Changed` entry describing the externalized gated-tool
      config and the skill rewording, and bump `pyproject.toml`'s version as a minor release
      (additive configuration surface); run `uv lock` and commit the regenerated `uv.lock` in the
      same commit.
- [x] 3.3 In the PR description, flag clearly for the user: after merging and reinstalling, the
      8-digit gate protects nothing until `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` is set on this
      machine - name the exact env var and the six tool names it previously hardcoded, so the user
      can restore equivalent protection in their own shell profile / CCCS config.
