---
name: pm-pdata-do-update-consuming-skills
description: Use after `ccst pdata init --write` has migrated a ~/cc/<project>'s flat files into its pdata store - finds Claude Code skills under ~/.claude/skills/ that literally reference the project's old flat-file paths, rewrites direct reads/writes to the equivalent `ccst pdata` commands, and flags anything that cannot be rewritten mechanically for a human decision. Matches by literal path only. Triggers - "update the skills that read the old <project> files", "fix skills that use the old paths after the pdata migration", "/pm-pdata-do-update-consuming-skills"; also run automatically as a step of pm-pdata-do-init. Do NOT use for the project's own docs (pm-pdata-do-update-project-docs) or the migration itself (pm-pdata-do-init).
---

# pm-pdata-do-update-consuming-skills

Finds skills that read or write a project's old flat-file paths directly and switches them to the
equivalent `ccst pdata` command. Run it after `ccst pdata init --write` has cut a project's data
over.

## Run this in a fresh context

Run this skill in a fresh context, not inline in the session that just ran `ccst pdata init
--write`. The point of a fresh read is that it cannot rationalize away a stale reference it
half-remembers writing earlier in the same conversation. Either is acceptable:

- An orchestrating session dispatches it as an `Agent` subagent. The Agent tool cannot set a cwd,
  so the subagent's prompt must begin by changing into the project root (`cd ~/cc/<project>`).
- A new Claude Code session started in `~/cc/<project>`.

Running it inline in the migrating session is not acceptable.

## Step 1 - Verify project context

Check the cwd is a `~/cc/<project>` project directory with a `CLAUDE.md`:

```sh
pwd && basename "$(pwd)" && ls CLAUDE.md 2>/dev/null && echo "project CLAUDE.md confirmed"
```

If `CLAUDE.md` is present, the `basename` output is the project name to search for in Step 2. If
not, print the following and stop:

> ERROR: This skill must be run from a `~/cc/<project>` directory containing a `CLAUDE.md`.
> Aborting without touching any skills.

## Step 2 - Find every skill referencing this project's old flat-file paths

Search every skill's `SKILL.md` and any scripts under its directory for literal mentions of this
project's pre-migration paths:

```sh
grep -rl "~/cc/<project>/<old-path>" ~/.claude/skills/*/ 2>/dev/null
grep -rl "cc/<project>/<old-path>" ~/.claude/skills/*/ 2>/dev/null   # relative-path equivalents
```

Substitute the project's actual name and the pre-migration path(s) from the project's
`ccst-pdata-init-write.log`, or from the classification report `ccst pdata init` produced. If
neither is available, check `<project-root>/.pdata-migrated/`: the migration archives the
original pre-cutover files there, unchanged, under their original relative paths. For example, a
project `home` that migrated its Tesco order history would search for `~/cc/home/data/tesco/*.csv`
or `home/data/tesco` across every skill's `SKILL.md` and scripts.

Match by literal path only. Do not widen the search to skills whose name merely sounds related to
the project or its domain: a skill named `do-tesco-shop` is not evidence on its own that it
touches this project's files. Only a literal path match is.

If Step 2 finds no matches at all, say so in the report and stop. There is nothing to do.

## Step 3 - Classify each match

For each skill with a literal path match, read the matching lines and enough surrounding
code or prose to be sure which category applies. Do not guess from the skill's name or its
one-line description.

- **Direct read/write** - the skill's script (or its documented instructions) opens, parses,
  appends to, or otherwise operates on the file at that path as part of normal execution. This
  needs rewriting.
- **Passing mention** - the path appears only in prose (an example, a comment about history, a
  reference to "where this used to live") and no code path touches the file. This may not need
  changing; leave it alone.

## Step 4 - Rewrite direct read/write matches

For each direct read/write, rewrite the file I/O to use the equivalent `ccst pdata` command:

- Reading to list or browse records: `ccst pdata list --project <name> --group <record_group>
  [--format json]`.
- Reading one record: `ccst pdata get --project <name> --id <id>`.
- Filtering or searching: `ccst pdata query --project <name> --group <record_group> --where
  '<field> <op> <value>'`.
- Appending a record: `ccst pdata add` (check `ccst pdata add --help` for the record shape the
  target record_group expects).
- Editing a record: `ccst pdata update` (version-checked; check `ccst pdata update --help`).

Keep the rewrite mechanical and narrow: change the I/O call to go through `ccst pdata`, and do not
otherwise restructure the skill's logic or prose beyond what reflects the new access path. If the
skill's script parsed CSV columns or JSON keys directly, map them to the pdata record fields using
`ccst pdata schema show --project <name> --group <record_group>` (it lists the group's registered
extension fields) rather than guessing field names.

Do not touch skills unrelated to this project. A match that on closer reading references a
different project's similarly-named folder is not this skill's concern - skip it.

## Step 5 - Flag anything unsafe to rewrite mechanically

Some direct matches do more than read a file or append a row: custom locking, multi-file
transactions, format transformations that do not map onto one `ccst pdata` command, or logic that
depends on file-level properties (mtime, file size, row order) that pdata does not expose the same
way.

Do not attempt to rewrite these. List each in the report as needing a human decision, with enough
detail (skill name, file, what it does, why it does not map cleanly) for the user to decide.
Silently skipping a match is not acceptable: every match found in Step 2 must appear in the
report as "updated", "left alone (passing mention only)", or "needs a human decision".

## Step 6 - Report

Summarise for the user:

1. Which skills had a literal path match (Step 2).
2. For each, its category (Step 3) and outcome: updated, left alone, or needs a human decision.
3. If there were no matches at all, say so plainly. That is a valid and complete outcome.
