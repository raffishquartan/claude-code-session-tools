## Context

`_print_hooks_install_table` builds fixed-width columns from the longest cell in each column
(`max(len(...))`), so the `Description` column is as wide as the longest description (~300 chars).
`lib/doctor.py::_wrap_reason` wraps free text with `textwrap.fill` to
`shutil.get_terminal_size(fallback=(80, 24))` with a hanging indent;
`cli/doctor-output-wrapping` is the precedent spec. `run_install_everything` runs the steps under
`contextlib.redirect_stdout(stream)` (a `StringIO` on the auto-apply path), but
`shutil.get_terminal_size` reads `COLUMNS` then `sys.__stdout__`, which `redirect_stdout` does not
touch: the capture wraps to the real terminal width, not 80.

Current bundle: 17 registrations across 14 unique hook names; `catchup`, `messaging-deliver` and
`pdata-sync` are each registered twice and repeat their description.

## Goals / Non-Goals

**Goals:**
- No events/description line wider than the effective width; a wrapped description can never look
  like a new record.
- Same visual language as `ccst doctor` non-OK results.
- Paths and commands in wrapped text stay copy-pastable.

**Non-Goals:**
- Reformatting the Skills step (max 62 columns) or the Shell / CLAUDE.md / Scheduled-jobs lines
  (max ~62). The `Dry run - ... <path>` lines stay unwrapped so paths stay copy-pastable.
- Changing `HOOK_DESCRIPTIONS` text or the doctor's reason text.
- Display-width (East Asian wide) handling: width is counted in characters; descriptions are
  code-owned ASCII.
- A general table library or new dependency.

## Decisions

- **Record layout: header (name, status) / `Events:` / description.** In-column wrapping leaves
  ~17 characters for the description at 80 columns, which is unreadable. Keeping events on the
  header line makes it up to 73 columns and unbounded for a custom `--source` bundle (a
  non-`ccst hooks run` command is named by its whole command string, `_bundle_inventory`), so the
  header carries only name and status and the events get their own wrappable line. The header is
  deliberately unwrapped; a name is an identifier, and tests locate a record by `startswith(name)`,
  which the spec now states. Alternative: in-column wrap on wide terminals, stacked on narrow;
  rejected as two layouts to maintain.
- **Drop the column-title row and rule.** A record layout has no columns to title.
- **Merge multi-event hooks by name** in the printer only; the inventory stays per-event because
  install logic needs that. Records stay in the existing alphabetical order (the inventory is
  sorted by hook name); events within a record keep bundle order.
- **Coarse merged status with `(new)` markers.** A merged status of `install` alone would hide
  which event is missing (partial state is real: an upgrade can add an event to an existing hook).
  The `(new)` marker keeps that information, only when statuses actually differ.
- **Shared wrapper in `lib/`.** `_wrap_reason` in `lib/doctor.py` stays as a delegating alias so
  `tests/test_ccst_doctor.py` (which imports it and monkeypatches `shutil.get_terminal_size`) is
  untouched, and the shared helper must call `shutil.get_terminal_size` through the module
  attribute so those patches still apply. Alternative: copy it; rejected per the
  one-source-of-truth standard.
- **`break_long_words=False, break_on_hyphens=False`** in the shared helper: live doctor output
  already splits `.pdata-`/`migrated` and `claude-`/`flags.json`, and hook descriptions are full of
  paths and backticked commands. Consequence: an over-long token overflows, so the width
  requirements carve that out. Because doctor shares the helper, `cli/doctor-output-wrapping` is
  updated in the same change.
- **Width floor of 40.** `shutil.get_terminal_size` passes `COLUMNS=1` straight through and
  `textwrap` with a 4-space indent then emits 5-character lines; the effective width is
  `max(detected, 40)`.
- **Wrap doctor's `Tip:` line** (86 characters, a fixed literal) through the same helper, since it
  over-runs every 80-column terminal and is the only other over-wide non-path line.

## Risks / Trade-offs

- [Output is taller: 14 records x ~3-5 lines instead of 18 rows] -> the step is already the
  longest; merging removes 3 repeated descriptions.
- [Per-event status was visible as separate rows] -> `(new)` marker on partly-installed hooks.
- [Doctor output changes for reasons containing paths/hyphens] -> intended fix; spec updated;
  existing doctor tests must still pass.
- [Tests coupled to the old layout] -> `tests/test_ccst_hooks_install_zero_arg.py` updated in this
  change; tests pin `COLUMNS` in the subprocess environment so a developer's exported value cannot
  change the result.
- [Anyone parsing the table] -> human-only stdout with no documented format and no `--json` mode.
