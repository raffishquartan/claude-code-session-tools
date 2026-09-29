Note: this change was implemented and verified before this task list was written (see
proposal.md - this is a retroactive backfill). All tasks below are marked complete to reflect
that; the checkboxes record what was done and how it was verified, per this schema's convention.

## 1. Section registry

- [x] 1.1 Replace the single `_BLOCK`/`_SENTINEL_START`/`_SENTINEL_END` constants in
      `claude_md_install.py` with a `SECTION_IDS` tuple and `_SECTION_BODIES` dict keyed by
      section id, plus `_sentinel_start`/`_sentinel_end`/`_block_text` helpers - verified by
      `tests/test_claude_md_install.py`'s byte-for-byte `messaging` pin test passing.
- [x] 1.2 Add the `workflow`, `agents`, and `confirm-gate` section bodies with the exact approved
      text - verified by reading the rendered block text in a test and by manual `--target
      <scratch-file>` dry-run smoke test showing all four `would add block` lines.
- [x] 1.3 Generalize `_find_block`/`_validate_sentinels` to take a `section_id` parameter, scoping
      malformed-marker detection to one section's sentinels - verified by the "malformed `agents`
      block doesn't block `--section workflow`" test.
- [x] 1.4 Add `_resolve_sections` (validates unknown ids, defaults to `SECTION_IDS` when `None`) -
      verified by a test asserting `ValueError` on an unknown id before any file write.

## 2. Library API

- [x] 2.1 Add a `section` field to `MarkdownResult`; change `install_claude_md`/
      `uninstall_claude_md` to accept `sections: Sequence[str] | None = None` and return
      `list[MarkdownResult]`, one atomic write per call - verified by the full
      `tests/test_claude_md_install.py` suite passing, including default-all-four, subset, and
      uninstall-independence cases.

## 3. CLI

- [x] 3.1 Add a repeatable `--section {messaging,workflow,agents,confirm-gate}` argument to both
      `ccst claude-md install` and `ccst claude-md uninstall`, sourced from `SECTION_IDS` -
      verified by CLI-level tests and a manual smoke test showing per-section output lines.
- [x] 3.2 Update `_cmd_claude_md_install`/`_cmd_claude_md_uninstall` to loop over the returned
      results and print one `path [section]: message` line each; update `--help` text and the
      module docstring's command reference - verified by manual invocation and CLI tests.
- [x] 3.3 Confirm `ccst install-everything`'s existing bare `install_claude_md(target,
      apply=apply)` call site needed no change (no `section` attribute on its `argparse.Namespace`
      resolves to "all four") - verified by `test_ccst_install_everything.py` passing unchanged.

## 4. Related fixes surfaced by the same review

- [x] 4.1 Fix `do-executor-critic-assessor-loop/SKILL.md`'s reference to `agent-usage.md` (a
      personal, unshipped file) to point at the shipped `agents` CLAUDE.md section instead -
      verified by manual inspection of the updated file.
- [x] 4.2 Fix `session_tag` hook's `SessionStart` message to stop referencing an undefined
      "CLAUDE.md startup flow" / "hooks report" - verified by all 21 tests in
      `tests/test_session_tag.py` passing unchanged (no test asserted on the removed phrases).

## 5. Verification

- [x] 5.1 Full test suite (`uv run pytest -q`) passes with no failures/errors (pre-existing skips
      only) - run twice independently (once by the implementing agent, once by the dispatcher).
- [x] 5.2 `mypy` clean on both touched library/CLI files.
- [x] 5.3 Manual smoke test: dry-run `ccst claude-md install --target <scratch file>` (all four
      sections) and `--section workflow` (one section) against a scratch file, confirming output
      format and that an unrequested section is left alone.
