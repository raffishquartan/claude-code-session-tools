## Why

A CCST-vs-CCCS inventory (this session, 2026-09-12) found that `src/hooks/confirm_8digit.py`'s
`GATED_TOOLS_DEFAULT` hardcodes one user's personal MCP tool names (Tesco, WhatsApp, GWR,
Google Workspace) directly inside CCST - a confirmed-public GitHub repo and PyPI package
(`gh repo view`: `isPrivate: false`). Two bundled skills (`pm-pdata-do-init`,
`pm-pdata-resolve-conflicts`) also name the maintainer by first name and describe his specific
two-machine setup. Both violate CCCS's own stated design intent (`config/CLAUDE.md` frames CCCS,
not CCST, as the repo allowed to hold personal configuration/PII) and the project's own
no-personal-identifiers coding standard.

## What Changes

- `confirm_8digit.py`'s gated-tool allowlist moves from a hardcoded Python constant to a runtime
  environment variable (`CCCS_CONFIRM_8DIGIT_GATED_TOOLS`, comma-separated tool names, matching
  the existing `CCCS_ENFORCE_8DIGIT`/`CCCS_MARKERS_DIR` naming convention for this hook's other
  personal-policy knobs). CCST ships with no default gated tools; a user's own config (in their
  shell profile, not tracked in either repo) supplies the real list — the same pattern already
  used by this file's `NOTIFY_EMAIL` self-send exception.
- `verify()`'s signature and behavior are otherwise unchanged - it already took `gated_tools` as
  a parameter, so this only changes what `main()` passes in.
- The two skills' wording is reworded from the maintainer's first name and a description of his
  specific machines to generic second-person phrasing ("you", "the user") - no functional change,
  no capability/behavior change, so no delta spec accompanies this part.

## Capabilities

### New Capabilities
- `notify/confirm-8digit-gated-tools-config`: defines that the 8-digit confirmation gate's
  gated-tool list comes from an environment variable, not a hardcoded default, alongside the
  sibling `notify/confirm-8digit-telegram` capability.

### Modified Capabilities
(none)

## Impact

- `src/hooks/confirm_8digit.py`: remove the hardcoded `GATED_TOOLS_DEFAULT` list; add an
  env-var-reading helper; update `main()` to use it.
- `tests/test_confirm_8digit.py`: replace the imported `GATED_TOOLS_DEFAULT` fixture with a
  test-local constant of the same tool names (tests exercise `verify()` directly and were only
  borrowing the production constant for convenience - decoupling this was correct regardless of
  the personal-data issue); add tests for the new env-var-parsing helper and for `main()`'s
  env-var-unset/set behavior.
- `src/cc_session_tools/skills/pm-pdata-do-init/SKILL.md`,
  `src/cc_session_tools/skills/pm-pdata-resolve-conflicts/SKILL.md`: reword away from the
  maintainer's first name and specific hardware description.
- No change to `~/.claude` on-disk format, no new CLI flag/subcommand on `ccst` itself.
- Minor version bump (`pyproject.toml`, `CHANGELOG.md`) - additive configuration surface (the new
  env var), no breaking change to any documented interface.
- Follow-up, explicitly out of scope for this change (cross-repo, CCCS-side): (a) setting
  `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` on this machine so the gate keeps protecting the same tools
  it did before this change - until set, the hook is a no-op for every tool; (b) moving six
  generic CCCS skills into CCST; (c) fixing CCCS's stale post-rename documentation
  (`cccs_hooks`→`hooks`, wrong hook count, stale `pm-*` skill names).
