## Why

`ccst install-everything` (and `ccst hooks install`) prints the Hooks step as a four-column table
whose last column, `Description`, is left unbounded: one row is ~377 characters wide. On any real
terminal each row hard-wraps at the screen edge with no continuation indent, so the tail of a
description lands at column 0 under the next hook's row and reads as a new, unrelated line. The
separator rule is also as wide as the longest description. `ccst doctor` already solved the same
problem (see `cli/doctor-output-wrapping`); the install output should read the same way.

## What Changes

- The Hooks table is replaced by one record per hook: a header line (hook name and install
  status), an `Events:` line, then the description word-wrapped to the terminal width, all
  indented beneath the header. The column-title row and dashed rule are dropped. Records are
  separated by one blank line.
- A hook registered for several events (`catchup`, `messaging-deliver`, `pdata-sync`) is one
  record with its events listed together, so 17 registrations render as 14 records. When such a
  hook is only partly installed, the events that would be added are marked `(new)`.
- Width is `shutil.get_terminal_size()` (the `COLUMNS` variable, else the real terminal via the
  process's stdout file descriptor), falling back to 80 columns when neither is available, and
  never less than 40 columns. The in-process capture used by auto-apply still resolves the real
  terminal.
- The shared wrapper stops splitting inside a token: paths, backticked commands and hyphenated
  names stay intact (a single token longer than the width is allowed to overflow). This also
  changes `ccst doctor`'s reason wrapping, which today produces breaks like `.pdata-` / `migrated`.
- `ccst doctor`'s fixed `Tip: run ccst install-everything --apply ...` line (86 characters) is
  wrapped by the same helper. The `Dry run - ... <path>` lines are left unwrapped so the path stays
  copy-pastable. No other step of `install-everything` exceeds 80 columns.
- Existing `tests/test_ccst_hooks_install_zero_arg.py` table assertions are updated to the new layout.
- Patch release: 3.7.1, filed under `### Fixed` (same class as the 3.3.3 doctor wrapping fix).

## Capabilities

### New Capabilities
- `cli/install-hooks-table-layout`: how the hooks install output is laid out so no description or
  events line exceeds the terminal width and a wrapped description is never mistaken for a new record.

### Modified Capabilities
- `cli/doctor-output-wrapping`: the reason block no longer splits words mid-token, and the wrap
  width has a 40-column floor; a single over-long token may exceed the width.

## Impact

- `src/cc_session_tools/cli/ccst.py`: `_print_hooks_install_table` (used by `ccst hooks install`
  and, through it, `install-everything`).
- `src/cc_session_tools/lib/doctor.py`: `_wrap_reason` delegates to a shared wrapper; the `Tip:`
  line goes through it.
- Tests: `tests/test_ccst_hooks_install_zero_arg.py` updated; new width/wrapping tests.
- `CHANGELOG.md`, `pyproject.toml` version 3.7.1, regenerated `uv.lock`.
- Human-only stdout: no JSON/machine-readable output is affected.
