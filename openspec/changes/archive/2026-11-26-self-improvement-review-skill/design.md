## Context

See proposal.md (Why). The previous changes (`2026-11-26-self-improvement-port`, `2026-11-26-self-improvement-jobs`) shipped the automated loop and left the interactive review skill to a config repository. That skill calls the config repository's bash scripts, hand-writes `reviews.log` lines with `printf` into a fixed path, computes the headless cost with `awk`, and names one maintainer's setup.

Bundled skills are directories under `src/cc_session_tools/skills/` with a `SKILL.md`; `ccst skills install` symlinks every such directory into `~/.claude/skills/`, `ccst skills uninstall` removes the symlinks, and `ccst doctor` runs `check_skill_symlink` for each one. No per-skill registration exists, so adding the directory is all the install, sync, uninstall and doctor coverage needs.

## Goals / Non-Goals

**Goals:**
- The interactive review ships with CCST, so the nudge and the FAIL message always point at something installed.
- The skill uses CCST commands for every state-file read or write, so it follows `CCST_DATA_HOME` and the log format stays defined in one place.

**Non-Goals:**
- Changing how `ccst skills install` treats an existing non-symlink directory.
- Changing the backlog or state-file formats, or the doctor checks.

## Decisions

### D1. Skill content
Copied from the config repository's version and kept behaviourally the same (Modes A, B, C, the lever taxonomy, the entry schema, the Step 6 checkpoint, "always record"). Changed:
- Personal names become "the user"; the config-repository specifics (symlinked backlog, `config/CLAUDE.md`, messages addressed to one project, the 8-digit gate) become generic wording: follow a symlinked backlog with `readlink -f`, any `CLAUDE.md` or imported file needs the exact replacement text shown first, messages about the Claude configuration itself.
- Mode C step 1 runs `ccst self-improvement status` (set up or not, snapshot, last reviews) instead of reading `signals.env` directly; when the loop is not set up the skill tells the user to run `ccst self-improvement init`.
- Mode C step 7 takes the headless cost from the `status` line `headless cost since last interactive review`, which `state.headless_cost_since_last_review` already computes with the same rule as the old `awk`.
- Mode B step 9 and Mode C step 8 run `ccst self-improvement record-review` (D2).
- The "Automated loop" section names the bundled jobs and `ccst` commands; the bash registration commands are gone.
- Reliability rows are read from `~/.claude/skills/*/references/*.md`, the same glob the collector counts.

### D2. `ccst self-improvement record-review`
`--mode` (choices `B`, `C`), `--findings`, `--applied`, `--deferred` (required non-negative integers; argparse type rejects negatives). Appends `<now ISO> mode=<M> findings=<n> applied=<n> deferred=<n>` to `reviews.log`, creating the state directory, and prints the line. `state.append_log_line` is shared with the headless review's logger so both writers append the same way. Alternative: keep `printf` in the skill. Rejected: it hardcodes `~/.local/share/claude` (wrong under `CCST_DATA_HOME`) and duplicates the format the parser in `state.py` depends on.

### D3. Messages always name the skill
`signals.review_command()` and `review_skill_installed()` are removed; the nudge and escalation use `signals.PERIODIC_REVIEW_COMMAND`. `report.review_line()` always prints `interactive review: run /review-session-and-improve-claude-setup periodic in a session`. A user who removed the skill by hand (`ccst skills uninstall --skill ...`) still gets the command name; the next install sync restores it. `STATUS_COMMAND` goes.

### D4. Existing unmanaged skill directory
`_decide_action` returns `NON_SYMLINK_EXISTS` for a real directory; without `--force`, `ccst skills install --apply` prints `error: <dest> exists and is not a symlink; use --force to move it aside` and exits 1. `install-everything` therefore fails, the automatic install sync records the failure (6 h backoff), and `ccst doctor` reports `skill:review-session-and-improve-claude-setup` FAIL (`exists but is not a symlink`) and, after a failed auto-sync, `install:synced` FAIL. The behaviour is kept (it never deletes user files); the README and CHANGELOG tell such users to delete the old directory (or move it outside `~/.claude/skills/`) and run `ccst install-everything --apply`. `--force` moves it to `<name>.bak-<timestamp>` inside `~/.claude/skills/`, where it still holds a `SKILL.md` with the same `name:`; the docs say to delete that backup. Verified by a test with a temporary HOME.

### D5. Not-set-up digest lines
Already satisfied: the collector line ends `, loop not set up (run ccst self-improvement init)` and the auto-review summary is `self-improvement auto-review: skipped (loop not set up: run ccst self-improvement init)`; the scheduler keeps the first 1000 characters of stdout, so both reach the digest whole. A test asserts both lines fit and name `init`.

## Risks / Trade-offs

- [Existing users with a copied skill directory get a failing sync after upgrade] → documented one-off step; doctor names the path; nothing is deleted automatically.
- [A config repository that recreates the directory on its own sync] → outside CCST; its users should stop installing that copy once CCST bundles it.

## Migration Plan

Upgrade, then if `~/.claude/skills/review-session-and-improve-claude-setup` is a real directory, delete it and run `ccst install-everything --apply`. Rollback: `ccst skills uninstall --skill review-session-and-improve-claude-setup`.
