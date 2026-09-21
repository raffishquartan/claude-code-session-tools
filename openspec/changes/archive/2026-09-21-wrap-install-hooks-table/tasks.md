## 1. Shared wrapping helper (red first)

- [x] 1.1 Write failing tests for a shared wrap helper: width 80 and 200, `COLUMNS=20` floor of 40, non-tty fallback of 80 with `COLUMNS` unset, a path and a hyphenated name never split, an over-long token overflows unsplit; verify they fail for the right reason
- [x] 1.2 Implement the helper in `lib/` (calls `shutil.get_terminal_size` via the module attribute; `break_long_words=False, break_on_hyphens=False`); verify 1.1's tests pass
- [x] 1.3 Make `lib/doctor.py::_wrap_reason` a delegating alias and wrap the `Tip:` line with the helper; verify `tests/test_ccst_doctor.py` passes without edits and a new test shows the Tip line never exceeds 80 columns at `COLUMNS=80`

## 2. Hooks record layout (red first)

- [x] 2.1 Update the table tests in `tests/test_ccst_hooks_install_zero_arg.py` (expected headers, descriptions, install status, already-installed after apply, `--hook` selector, mixed status after partial install) to the record layout, joining an record's wrapped lines before matching descriptions; verify they fail against the current implementation
- [x] 2.2 Add tests: no events/description line exceeds 80 at `COLUMNS=80` (subprocess env pinned with `COLUMNS`, plus a case with it removed); one record per hook name (17 registrations -> 14 records); `(new)` marker only on a partly-installed multi-event hook; no description -> header and `Events:` only, using a custom `--source` bundle whose command is not `ccst hooks run <name>`; one blank line between records and none trailing; record's first line starts at column 0 with the name; verify they fail
- [x] 2.3 Rewrite `_print_hooks_install_table` to group by hook name and print header / `Events:` / wrapped description; verify 2.1 and 2.2 pass

## 3. Verify end to end

- [x] 3.1 Run `CCST_NO_AUTO_SYNC=1 uv run python -m cc_session_tools.cli.ccst install-everything --no-pypi` and `ccst hooks install --target <empty tmp settings>` (dry run) at `COLUMNS` 60, 80 and 200 and confirm no events/description/Tip line exceeds the width and the other steps' output is unchanged

## 4. Release

- [x] 4.1 Add a `### Fixed` entry under a new `## [3.7.1]` heading in `CHANGELOG.md`; bump `pyproject.toml` to 3.7.1, run `uv lock`, and verify `uv.lock`'s `cc-session-tools` entry shows 3.7.1
- [x] 4.2 Run the full suite (`uv run pytest -q`) and the repo's lint/type checks and verify every check exits 0
- [x] 4.3 Sync the delta specs and archive the change; verify `openspec/specs/cli/install-hooks-table-layout/spec.md` exists and `openspec/specs/cli/doctor-output-wrapping/spec.md` shows the new requirement text
