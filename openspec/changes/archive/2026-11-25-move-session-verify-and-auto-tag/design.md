## Context

See proposal.md - Why. `move_session.py` builds an ordered list of path-string replacements (long path, encoded project key, tilde form, and on MOVE+RENAME the `cc-sessions/<tag>` substring) and applies them to the copied transcript. `verify_dst_jsonl()` then compares the destination's `cwd` set against `{dst_cwd_abs}` alone. In `--execute`, the session directory, transcript and (sometimes) memory are copied before verification; a failure returns 1 without undoing them. Destination rules live in `cc_session_tools.lib.rules.check_session_destination()`, shared with `ccd`.

## Goals / Non-Goals

**Goals:**
- Verification accepts exactly the cwd values the rewrite is expected to produce.
- A failed `--execute` verification leaves the filesystem as it was before the run.
- A MOVE into a strict-root project with a non-compliant tag succeeds with a clearly reported derived tag.

**Non-Goals:**
- Rolling back on exceptions after verification has passed (memory copy, tombstone, sessions.db). Review follow-up: an exception during the copy and rewrite step (copy errors, disk full, interruption) rolls back like a failed verification.
- Smarter tag derivation (fuzzy prefix matching, synonyms). The rule is deliberately mechanical.
- Changing the shared `check_session_destination()` contract used by `ccd`.

## Decisions

### Expected cwd set is the source cwd set rewritten
A pure helper `apply_replacements(text, replacements)` applies the replacement list in order; `rewrite_jsonl_paths()` uses it on the file text and verification uses it on each source `cwd`. `verify_dst_jsonl()` takes the replacement list, reads the source `cwd` set, and computes `expected = {dst_cwd_abs} | {apply_replacements(c) for c in src_cwds}`; `cwds_clean = dst_cwds <= expected`. The unexpected values are returned and printed. Sharing one helper guarantees the expectation and the actual rewrite cannot drift.
- Alternative: allow any cwd under the destination cwd or the destination key directory. Rejected: a path-prefix allowlist accepts values the rewrite did not produce and hides real corruption.
- A residual source string anywhere in the text is still caught by the existing `remaining_long` / `remaining_key` counts in strict mode, so no extra containment check on cwd values is needed.

### Rollback of exactly what the run created
Before each create step, `main()` records the path in a `created` list only if it did not already exist: `<dst-cwd>/cc-sessions` (directory, if new), the destination session directory, the destination project key directory (if new), and the destination transcript. On strict verification failure, `rollback_created()` walks the list in reverse: a file is unlinked, the session directory (which the clobber check proved did not exist) is removed recursively, and a parent directory recorded as new is removed only if empty. Each removal is printed; any `OSError` is caught per path and the path is listed under "could not remove - delete by hand". Python's own file operations are not subject to the shell deny hook, so this works where a suggested `rm` would not.
- Verification moves ahead of the memory copy, so memory is never part of the rollback set.
- Alternative: print the paths and leave them. Rejected: the user then has to delete files the hook blocks them from deleting, and a re-run is rejected until they do.
- Alternative: copy into a temporary location and rename into place after verification. Rejected as a larger restructuring for a patch-sized fix; the created-list approach touches only the failure path.

### Tag derivation as a retry of validation, not a new rule
`derive_strict_tag(src_tag, src_project, dst_project)`: split the date prefix; remainder = suffix with one leading `<src_project>-` removed (if present); if remainder starts with `<dst_project>-` use it as the new suffix, else `<dst_project>-<remainder>`. `main()` triggers it only when: no `--rename-tag`, no `--force`, no `--no-auto-rename`, cwd changes, and the first `check_session_destination()` call fails with exactly one error, the one `validate_strict_tag_suffix(src_suffix, dst_project)` returns (only a strict-root destination produces it). It then re-runs `check_session_destination()` with the derived tag (which also applies `validate_new_tag`, so the date prefix and format are checked). If that passes, the derived tag becomes `dst_tag` and the operation is MOVE+RENAME everywhere downstream (picker label, `session_tags`, `cc-sessions/<tag>` rewrite, live-holder refusal). If not, the script fails with the original errors plus the derived tag's errors.
- A remainder equal to `<dst_project>` is treated like an empty one, so `<date>-garden` into `garden` never becomes `garden-garden`.
- The derived tag then meets the existing clobber check, which aborts naming the existing path.
- A suffix exactly equal to `<src_project>` yields an empty remainder, giving suffix `<dst_project>`, which the strict rule rejects (no label after the prefix); the script then fails with both error sets. Not special-cased.
- Collision case: `scratch-build-shelf` into `building` gives `building-build-shelf`. The rule does not try to merge `build-` with `building-`; a user who wants a different tag passes `--rename-tag`.

### Notice text
One helper formats the NOTE line; it is printed in PLAN, after the EXECUTING header and in DONE beside the resume command, followed by the picker label. `print_in_session_recipe()` already passes `--rename-tag <dst_tag>` when the tag changes, so the refusal recipe re-runs with the derived tag explicitly.

### Version
A new flag and new default behaviour are a backwards-compatible feature, so the repo's version policy calls for a minor bump (4.6.0), not the patch bump the original request suggested.

## Risks / Trade-offs

- [Accepting any rewritten source cwd lets an odd but legitimate source cwd (for example `/tmp`) through unchanged] -> That value was in the source and the rewrite did not touch it; failing on it is the same false positive class this change fixes.
- [Auto-rename changes the tag the user expected] -> The NOTE is printed in dry-run, execute and DONE; dry-run is the default; `--no-auto-rename` restores the hard failure.
- [Rollback deletes data] -> Only paths recorded as created by this run, after the clobber check proved they did not exist; parent directories only when empty.
