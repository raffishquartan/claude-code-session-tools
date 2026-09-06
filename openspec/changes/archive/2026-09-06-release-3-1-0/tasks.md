## 1. Telegram notification for confirm_8digit

- [x] 1.1 Write failing tests: a blocked call sends a Telegram notification (mock `send_telegram`,
      assert it was called with the block message); a warned call also sends one; an allowed
      call sends none; a `send_telegram` failure does not change the gate's exit code or stderr
      message. Implement by calling `send_telegram()` in `verify()` at each blocking/warning
      `VerificationResult` construction (design.md Decision 1). **Incident found during this
      task**: this machine has real `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env vars set, and
      the existing tests' `monkeypatch.setenv("HOME", tmp_path)` does not stop
      `_credentials()` from finding them (it reads those two vars directly before falling back
      to `~/.creds`). The first unmocked full-file test run genuinely sent real Telegram
      messages to the user's phone for every pre-existing test that reaches a block/warn path.
      Fixed by adding an autouse `_no_real_telegram_sends` fixture stubbing `send_telegram` for
      every test in the file by default; the 5 new Telegram-specific tests override it locally.
      Flagged to the user directly, not just recorded here.
- [x] 1.2 Full test suite green for `confirm_8digit`.

## 2. ccmsg list shows message age

- [x] 2.1 Write a failing test: `MessageRow` has a `sent_at` field, populated by `list_messages()`
      from the underlying `Message`. Implement.
- [x] 2.2 Write a failing test: `ccmsg list`'s printed rows include the message's relative age.
      Implement in `_cmd_list`. `_relative_age` renamed to public `relative_age` (dropped the
      leading underscore) since it now has a genuine cross-module consumer (`ccmsg.py`), not
      just internal callers within `service.py` - matches this repo's "hoist to shared location
      once 2+ consumers exist" convention.

## 3. add-field rejects a type-mismatched rerun

- [x] 3.1 Write failing tests for a new `_extension_column_type(conn, table, field_name) -> str |
      None` helper in `lib/pdata/repository.py`: returns the stored type for an existing column,
      `None` if the column doesn't exist. Implement.
- [x] 3.2 Write failing tests for `add_extension_column()`: rerunning with a different `sql_type`
      than the existing column raises `ValueError` naming both types, and makes no schema
      change; rerunning with the *same* `sql_type` still succeeds as a no-op (existing behavior
      unchanged); the existing
      `test_schema_add_field_rerun_updates_description_without_duplicating_column` test (and any
      other same-type rerun test) still passes unmodified. Implement (design.md Decision 3).
- [x] 3.3 Write a failing CLI-level test: `ccst pdata schema add-field` with a mismatched type on
      an existing field exits 2 with a clear stderr message. Confirmed the existing generic
      `except ValueError` in `_cmd_pdata_schema_add_field` already handles it correctly - no new
      production code needed here.

## 4. Document add-field's description-edit behavior

- [x] 4.1 **Scope correction**: `add-field` was not documented in `README.md` anywhere before
      this change (confirmed via grep across `README.md` and `docs/`) - the handoff prompt's
      "wherever it is documented" assumed an existing home that doesn't exist; pdata has no
      README section at all. Adding a whole new pdata section is out of scope for this item, so
      this task is satisfied via 4.2's `--help` text (the one concrete, existing location named
      in the prompt) rather than a new README addition.
- [x] 4.2 Added the description-edit and type-immutability statements to `add-field`'s
      `--help` text via the subparser's `description=` (previously only had a short `help=`
      one-liner with nothing shown on `--help` itself).

## 5. Stale skill symlink doctor check

- [x] 5.1 Write failing tests for `check_no_stale_skill_symlinks(skills_target_dir: Path) ->
      list[CheckResult]`: a symlink whose target exists is not flagged; a symlink whose target
      does not exist is flagged (WARN, naming the symlink); a non-symlink entry is ignored; an
      empty/nonexistent `skills_target_dir` reports OK/no findings rather than erroring.
      Implement in `doctor.py` (design.md Decision 4, mirrors `check_no_stale_hooks`'s shape).
- [x] 5.2 Wire the new check into `run_all_checks` and verify `ccst doctor`'s output includes it.

## 6. Rename the two pm-* skills

- [x] 6.1 `git mv` `src/cc_session_tools/skills/pm-pdata-audit` to
      `src/cc_session_tools/skills/pm-pdata-do-audit-and-prepare-to-migrate`; update its
      `SKILL.md` frontmatter `name:` field and every reference to `pm-pdata-migrate` inside it to
      the new name.
- [x] 6.2 `git mv` `src/cc_session_tools/skills/pm-pdata-migrate` to
      `src/cc_session_tools/skills/pm-pdata-do-migrate`; update its `SKILL.md` frontmatter
      `name:` field and every reference to `pm-pdata-audit` inside it to the new name.
- [x] 6.3 Re-grepped `pm-pdata-audit`/`pm-pdata-migrate` across `src/` and `docs/` - clean.
      Also caught and fixed one pre-existing test
      (`test_pm_pdata_audit_and_migrate_are_bundled_skills`) that asserted the old bundled
      names, missed by the grep sweep since it constructs the names from string literals that
      matched but weren't caught by the initial pass until the full suite ran red.
- [x] 6.4 Manually exercised end-to-end in a real sandbox: seeded pre-rename symlinks pointing
      at genuinely nonexistent targets, ran `ccst skills install --apply` from the renamed
      source (installs the two new names correctly, does not touch the old symlinks), then
      `ccst doctor` (flags both old symlinks as `[WARN] skills:stale:pm-pdata-audit` /
      `skills:stale:pm-pdata-migrate` with the exact `rm` remediation).
- [x] 6.5 Sent `ccmsg` `20260906T004844Z-96f1` to `claude-code-config-sync`, referencing the
      original `20260904T235515Z-7778` message and naming both new skill names.

## 7. Version bump and release artifacts

- [x] 7.1 Bump `pyproject.toml`'s `version` to `3.1.0`, run `uv lock`, commit both together.
- [x] 7.2 Write the `[3.1.0]` `CHANGELOG.md` entry covering all 5 items.
- [x] 7.3 Full test suite green (`uv run pytest -q`, real `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
      stripped via `env -u` as an extra safety net on top of the autouse fixture from task 1.1)
      and `mypy` clean on every touched production file.
- [x] 7.4 `openspec-sync-specs` (4 new main specs created and validated) +
      `openspec-archive-change` for `release-3-1-0`.
- [x] 7.5 Recommend a PR title/body (style matching recent merged PRs) and confirm with the user
      before running `gh pr create`. No attribution lines in commits or the PR description.
      Opened as PR #137.
- [x] 7.6 Mark Task #6 `completed` via `TaskUpdate` once the PR is open.
