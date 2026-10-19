## Context

The skill is prompt text only (`SKILL.md`); behaviour changes by rewording. See proposal.md for the failure. A live check with `claude -p`, SKILL.md prepended to a long synthetic session, gave nested bullet lists in 4 of 4 runs with the current wording.

## Goals / Non-Goals

**Goals:**
- Make the single-line requirement unmissable and unambiguous in the skill text.

**Non-Goals:**
- No change to which content the paragraph covers, the WORKLOG handling, or the review step's accept/edit/exclude behaviour.
- No programmatic post-processing; the skill has no code.

## Decisions

- State the format as a hard requirement in "What to draft", repeat it in "Confirmation step" and "Final output". Alternative: one mention only; rejected because the model follows the nearest structural cue (the numbered list) over a single distant sentence.
- Mark the five-part numbering as "for you, not for the output" and prescribe inline labels for the two task groups. Alternative: renumber as prose; rejected as a larger rewrite for no gain.
- Show the draft as the literal command for review so review and paste cannot diverge.

## Risks / Trade-offs

- [Model still emits a list in some runs] -> Live-tested before and after; wording repeated in three places.
- [Very long single line is harder to review] -> Accepted; `/compact` requires it.
