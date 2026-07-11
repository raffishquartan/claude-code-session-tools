# Calibrating HARNESS_BASELINE_TOKENS

`HARNESS_BASELINE_TOKENS` (in `measure.py`) is the one persistent contributor the
analyzer cannot measure directly: the core Claude Code system prompt plus the
built-in non-deferred tool schemas (Workflow, Agent, Bash, AskUserQuestion, Read,
Edit, Write, Skill, ToolSearch, ScheduleWakeup, SendUserFile). It currently holds
an unverified estimate. This is how to replace it with a measured value.

## Important caveat — two different tokenizers

- The analyzer counts everything with **tiktoken** (`count-text-tokens`,
  gpt-4o-mini encoding) — a proxy.
- The real session input tokens reported below are **Anthropic** tokens.

These differ (typically within ~10-20%). The baseline you derive will be in
Anthropic units mixed into an otherwise tiktoken report. Treat the whole report
as a *relative* guide, not an exact byte count. If you want strict tiktoken
consistency instead, see "Alternative" at the bottom.

## Procedure (differential method)

1. **Start a fresh session** in your normal environment (real CLAUDE.md, skills,
   MCP all loaded — do NOT strip anything; we want the real baseline).

2. **Send one trivial message** (e.g. `hi`). Let it answer. Do nothing else.

3. **Read the first assistant turn's token usage** from the transcript JSONL at
   `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`. Find the first
   `assistant` message and read its `message.usage`:

   ```
   F = input_tokens + cache_creation_input_tokens
   ```

   On a cold first turn `cache_read_input_tokens` is ~0; if it isn't, add it too.
   `F` is the full persistent context: harness baseline + every measured
   contributor.

4. **In that same session, measure the contributors** with the analyzer, using a
   FULL deferred-tool capture (not a truncated one). Temporarily set
   `HARNESS_BASELINE_TOKENS = 0`, run `/reduce-persistent-context`, and read
   `total_tokens` from `context-report.json`. Call it `M` (this is the sum of
   everything EXCEPT the harness, because you zeroed it).

5. **Compute and set the baseline:**

   ```
   HARNESS_BASELINE_TOKENS = F - M
   ```

   Put that number in `measure.py` and update the comment with the date and the
   measured `F` and `M` you used.

6. **Verify:** re-run the analyzer; `total_tokens` should now ≈ `F`.

## Measured results log

- **2026-06-20** — session `20260620-oneshot-find-baseline` (`~/cc/oneshot`),
  transcript `~/.claude/projects/<encoded-cwd>/9db664e2-...jsonl` (path pattern —
  see Step 3 above for how to locate your own).
  First assistant turn ("hi", cold, `cache_read`=0):
  `input_tokens` 25,481 + `cache_creation_input_tokens` 34,624 = **F = 60,105**
  Anthropic tokens (full persistent context). A full-capture analyzer run in the
  same session measured **M = 22,197** tiktoken (claude_md 9,349, mcp_names 5,549,
  skill_desc 5,010, hooks 1,804, mcp_instructions 408, deferred builtin 77).
  → **`HARNESS_BASELINE_TOKENS = F - M = 37,908`** (set in measure.py).

## Alternative — don't estimate it at all

If you would rather keep the report in pure tiktoken units with no mixed-unit
fudge, set `HARNESS_BASELINE_TOKENS = 0` and treat the harness as explicitly
out of scope (like the deferred MCP schemas already are). Note in the report
that a fixed, unmeasured harness overhead sits on top of every number. This is
the most honest option if exactness matters more than completeness.
