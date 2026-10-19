## Why

`/compact` accepts a single string, and the user pastes the `compact-smart` result straight into it. The skill's wording never says the paragraph must be one line, and its numbered five-part structure plus the "two distinct groups" of outstanding tasks invite multi-paragraph prose and bulleted lists. In long sessions the skill reliably returns nested bullet lists, which cannot be pasted into `/compact` as one string.

## What Changes

- Add an explicit, hard single-line format requirement to the `compact-smart` skill: one line of plain prose, no line breaks, no bullets, no numbered lists, no headings, no markdown.
- Tell the skill that its five-part structure is a content checklist for the sentences, not an output layout, and to label the Claude/user task groups inline.
- Have the confirmation step show the literal `/compact <paragraph>` command on a single line in a code block, so what is reviewed is exactly what gets pasted.
- Patch release (no interface change).

## Capabilities

### New Capabilities

### Modified Capabilities
- `skills/compact-smart`: adds a requirement that the drafted paragraph and the command presented to the user are a single line with no line breaks or list markup.

## Impact

- `src/cc_session_tools/skills/compact-smart/SKILL.md` (wording only).
- `openspec/specs/skills/compact-smart/spec.md` (new requirement after sync).
- `pyproject.toml`, `uv.lock`, `CHANGELOG.md`: patch bump 3.16.1 to 3.16.2.
