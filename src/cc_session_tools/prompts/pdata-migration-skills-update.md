# pdata migration — consuming-skills update

This file is a self-contained prompt intended to be run after `ccst pdata init --write` has
migrated a project's flat-file data into its pdata store. It walks through finding and updating
any Claude Code skills that read or write that project's old flat-file paths directly, so they
use the equivalent `ccst pdata` command instead.

Run it via:

```sh
cd ~/cc/<project> && \
  claude -p "Check that you are executing with a ~/cc/<project> directory as your cwd. If you are not then exit. If you are then use this file as your prompt: <path-to-cc-session-tools>/src/cc_session_tools/prompts/pdata-migration-skills-update.md"
```

---

## Step 1 — Verify project context

Before doing anything else, check that the current working directory is a `~/cc/<project>`
project directory.

Run:
```sh
pwd && basename "$(pwd)"
```

Confirm with the user (or from the project's own `CLAUDE.md`, if present) that this is the
project name to search for. If the directory clearly isn't a `~/cc/<project>` project, print the
following message and exit immediately:

> ERROR: This prompt must be run from a `~/cc/<project>` directory. Aborting without touching any
> skills.

---

## Step 2 — Find every skill referencing this project's old flat-file paths

Search every skill's `SKILL.md` and any scripts under its directory for literal mentions of this
project's pre-migration paths:

```sh
grep -rl "~/cc/<project>/<old-path>" ~/.claude/skills/*/ 2>/dev/null
grep -rl "cc/<project>/<old-path>" ~/.claude/skills/*/ 2>/dev/null   # relative-path equivalents
```

Substitute the project's actual name and the actual pre-migration path(s) identified from the
project's `ccst-pdata-init-write.log` (or from the classification report `ccst pdata init`
produced) — e.g. for a project called `home` that migrated its Tesco order history, you'd search
for mentions of `~/cc/home/data/tesco/*.csv` or `home/data/tesco` across every skill's `SKILL.md`
and scripts.

Match by literal path only. Do not widen the search to skills whose name merely sounds related to
the project or its domain — a skill named `do-tesco-shop` is not evidence on its own that it
touches this project's files; only a literal path match is.

If Step 2 finds no matches at all, say so in your final report and stop — there is nothing to do.

---

## Step 3 — Classify each match

For each skill with a literal path match, read enough of the surrounding code/prose to determine
which of these two categories it falls into:

- **Direct read/write** — the skill's script (or its documented instructions) actually opens,
  parses, appends to, or otherwise operates on the file at that path as part of its normal
  execution. This needs rewriting.
- **Passing mention** — the path appears only in prose (an example, a comment about history, a
  reference to "where this used to live"), and no code path actually touches the file. This may
  not need changing.

Do not guess from the skill's name or its one-line description in the skills listing — read the
actual matching lines and enough surrounding context to be sure which category applies before
deciding.

---

## Step 4 — Rewrite direct read/write matches

For each skill classified as **direct read/write** in Step 3, rewrite the file-I/O to use the
equivalent `ccst pdata` command instead of touching the flat file:

- Reading a file to list/browse records → `ccst pdata list --project <name> --group
  <record_group> [--format json]`.
- Reading a specific record → `ccst pdata get --project <name> --id <id>`.
- Filtering/searching → `ccst pdata query --project <name> --group <record_group> --where '<field>
  <op> <value>'`.
- Appending a new record → `ccst pdata add` (check `ccst pdata add --help` for the exact record
  shape the target record_group expects).
- Editing an existing record → `ccst pdata update` (version-checked; check `ccst pdata update
  --help`).

Keep the rewrite mechanical and narrow: change the I/O call to go through `ccst pdata`, but don't
otherwise restructure the skill's logic or prose beyond what's needed to reflect the new access
path. If the skill's script parsed CSV columns or JSON keys directly, map those to the equivalent
pdata record fields using the project's schema (`ccst pdata schema --help` for the discovery
subcommand) rather than guessing field names.

**Do not touch skills unrelated to this project.** A skill whose path match turns out, on closer
reading, to reference a different project's similarly-named folder is not this prompt's concern —
skip it.

---

## Step 5 — Flag anything unsafe to rewrite mechanically

Some direct read/write matches will do something more complex than a simple read-a-file /
append-a-row operation — custom locking, multi-file transactions, format transformations that
don't map cleanly onto one `ccst pdata` command, or logic that depends on file-level properties
(mtime, file size, row order) that pdata doesn't expose the same way.

Do not attempt to mechanically rewrite these. Instead, list them explicitly in your final report
as needing a human decision, with enough detail (skill name, file, what it does, why it doesn't
map cleanly) that the user can decide how to proceed. Silently skipping a match without flagging
it is not acceptable — every match found in Step 2 must show up in the final report as either
"updated", "left alone (passing mention only)", or "needs a human decision".

---

## Step 6 — Report

Summarise for the user:

1. Which skills were found with a literal path match (Step 2).
2. For each: which category it fell into (Step 3), and the outcome — updated, left alone, or
   flagged for a human decision (Step 5).
3. If no matches were found at all, say so plainly — that's a valid and complete outcome.
