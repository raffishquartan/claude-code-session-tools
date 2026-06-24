<!--
Copyright (c) 2026 raffishquartan. All rights reserved.
Licensed for personal use only.
-->

# Design: `last-screenshot` UserPromptSubmit hook (`>lss`)

Date: 2026-06-16
Status: Approved (design); pending implementation plan.

## Problem

When the user has just taken a screenshot and wants Claude to look at it, they
currently have to find its filename and type the full path. They want a short
token - `>lss` ("last screenshot") - that they can drop into any prompt so
Claude picks up the most recent screenshot automatically.

## Summary

A `UserPromptSubmit` hook scans each submitted prompt for the token `>lss`.
When present, it resolves the newest image file in the configured screenshot
directory and injects its absolute path as additional context, together with an
explicit note telling Claude how to decide whether the user actually wants the
image. The hook only ever injects **text**; the image enters context only if
Claude then calls `Read` on the path.

## Goals

- Typing `>lss` anywhere in a prompt lets Claude refer to the most recent
  screenshot without the user specifying a path.
- The user can talk *about* the `>lss` feature without an image being attached.
- No personal paths committed to the repo; the screenshot directory is
  configurable.

## Non-goals (YAGNI)

- `>lss2`, `>lss:3`, or any "N-back" / multi-screenshot selection. v1 resolves
  the single newest screenshot only. The resolver is structured so this could
  be added later, but it is not built now.
- Clipboard capture. Confirmed the user saves screenshots to disk
  (Snipping Tool / Win+PrtScn), so a file-based resolver is sufficient.
- Forcing the image into context. A hook cannot inject image bytes; only
  Claude's `Read` tool can. This is by design (see Mechanism).

## Mechanism

1. CC fires `UserPromptSubmit` with a JSON payload on stdin containing a
   `prompt` field (same contract as the existing `prompt-guard` hook).
2. The hook reads the payload, checks `prompt` for the `>lss` token.
   - **No match** -> exit 0, no output. Near-zero cost on every prompt.
   - **Match** -> resolve newest screenshot, emit additional context, exit 0.
3. The injected context names the absolute path and tells Claude how to choose:

   > `[last-screenshot] The user's message contains ">lss". If they are asking
   > you to look at their latest screenshot, it is at <abs path> (taken Xm ago).
   > If they are only discussing the >lss feature itself, ignore this and do not
   > read the file.`

4. Claude decides:
   - **Literal request** ("summarise >lss") -> `Read` the path -> image enters
     context.
   - **Meta-talk** ("oh you mean >lss? yeah it's awesome") -> recognise it is
     not an instruction -> do not read -> no image, no noise.

Keeping the hook "dumb" (inject + let Claude judge) is deliberate: the hook
cannot see the conversation, so it must not try to guess intent itself.

## Token matching

Match the literal token `>lss` when it is **not** flanked by alphanumeric
characters, so surrounding punctuation and brackets are fine:

```
regex: (?<![A-Za-z0-9])>lss(?![A-Za-z0-9])
```

Matches: `>lss`, `>lss?`, `>lss.`, `(>lss is interesting)`, `loss at >lss.`
Does not match: `>lssfoo`, `process>lssbar` (token is part of a larger word).
Case-sensitive (lowercase `>lss`).

## Screenshot resolution

- Search the configured screenshot directory for image files
  (`*.png`, `*.jpg`, `*.jpeg`, case-insensitive).
- Select the newest by **file mtime** - not by parsing the
  `Screenshot YYYY-MM-DD HHMMSS` filename, because OneDrive sync can rewrite
  names; mtime is more reliable.
- Compute age = now - mtime.

## Staleness handling

- Always inject the newest screenshot's path.
- If age > 10 minutes, the injected note additionally warns, e.g.
  `(taken 47m ago - older than 10 min; confirm this is the one you meant)`.
- Never block; warning is advisory only. Consistent with the warn-not-block
  posture of the other UserPromptSubmit hooks.

## Configuration / portability

- The screenshot directory must not be hardcoded (CCST is shareable; no
  personal paths in committed code).
- Resolution order for the directory:
  1. Env var `CCST_SCREENSHOT_DIR` if set.
  2. Otherwise the install-time default written by the installer
     (templated `{{SCREENSHOT_DIR}}`, same pattern as `sleep-nudge/install.sh`).
- The committed source contains only the env-var name and the placeholder; the
  concrete path lives in the user's local config after install.

## Failure modes (all exit 0, never block)

- Configured directory missing or unset -> inject a one-line note that no
  screenshot directory is configured.
- Directory exists but contains no images -> inject a one-line note that no
  screenshot was found.
- Malformed/empty stdin JSON -> exit 0 silently (matches `prompt-guard`).

## Wiring

Mirrors the existing `prompt-guard` hook exactly:

| Piece | Location |
|-------|----------|
| Hook logic | `claude-code-session-tools/src/cccs_hooks/last_screenshot.py` (`main()` reads stdin JSON, writes context, returns 0) |
| Dispatch entry | `HOOK_VERBS["last-screenshot"] = "cccs_hooks.last_screenshot"` and a `HOOK_DESCRIPTIONS` entry in `src/cc_session_tools/cli/ccst.py` |
| Shell wrapper | `claude-code-config-sync/hooks/user-prompt-submit/last-screenshot.sh` -> `exec ccst hooks run last-screenshot` |
| Bundle entry | `claude-code-session-tools/config/hooks-bundle.json` UserPromptSubmit block |
| Settings registration | merged into `~/.claude/settings.json` via `ccst hooks install` |

## Module shape (`last_screenshot.py`)

Pure logic separated from I/O for testability:

- `find_token(prompt: str) -> bool` - regex match above.
- `resolve_screenshot_dir() -> Path | None` - env var, else configured default.
- `newest_screenshot(dir: Path) -> Path | None` - newest image by mtime.
- `build_context(path: Path | None, age_seconds: float | None, dir_configured: bool) -> str`
  - the injected note (literal/meta wording, staleness warning, failure notes).
- `main(argv=None) -> int` - read stdin JSON, gate on token, print context to
  the stream CC reads for `UserPromptSubmit` additional context, return 0.

## Tests

- Token match: positives (`>lss`, `>lss?`, `(>lss)`, `loss at >lss.`) and
  negatives (`>lssfoo`, `process>lssbar`, no token).
- `newest_screenshot`: selects the highest-mtime image among several; ignores
  non-image files; returns None on empty dir.
- Staleness: boundary at 10 minutes (just-under vs just-over) toggles the
  warning text.
- Failure notes: unset/missing dir and empty dir each produce their note.
- `main`: with a `>lss` prompt and a populated temp dir, emits context naming
  the file; with no token, emits nothing and returns 0; with malformed JSON,
  returns 0.
