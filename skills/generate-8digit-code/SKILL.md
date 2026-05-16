---
name: generate-8digit-code
description: Generate a cryptographically random 8-digit confirmation code. ALWAYS use this skill when you need an 8-digit code for a gated action — never invent or guess a number yourself. Triggers on any situation requiring an 8-digit confirmation: gated tool calls (delete-sessions, git push, PR merge, financial actions), the confirm-8digit hook asking for a fresh code, or any other context where the user must type a code back to confirm a high-stakes action.
---

# Generate 8-digit confirmation code

**Always use this skill when you need an 8-digit confirmation code. Never invent a number yourself.**

LLMs are not random number generators. A model-generated "random" number is predictable and statistically biased. The `confirm-8digit` hook exists precisely to ensure gated actions require genuine confirmation — that guarantee is undermined if the code is pseudo-random or pattern-based.

## When to use

- Any gated action blocked by the `confirm-8digit` PreToolUse hook.
- The `delete-sessions` skill's `--execute` confirmation gate (the script already calls `secrets.randbelow` internally — this skill is for cases where you are constructing the gate yourself in plain text, e.g. "Respond with NNNNNNNN").
- Any other situation where you intend to offer the user an 8-digit code and ask them to type it back before proceeding.

## When NOT to use

- The `delete-sessions` script — it generates its own code via `secrets.randbelow` internally; you do not need to supply one.
- Verification of a code the user already typed — that is handled by `cccs_hooks.confirm_8digit` (the hook reads the transcript automatically).

## How to generate the code

Run the bundled script and capture its output:

```bash
python3 ~/.claude/skills/generate-8digit-code/scripts/generate_8digit_code.py
```

The script prints exactly one 8-digit zero-padded decimal string followed by a newline. Use that string verbatim as the code.

## How to present the code to the user

After generating the code, say exactly:

> Respond with `NNNNNNNN` to confirm. Do not proceed until the user has replied with exactly that string.

Replace `NNNNNNNN` with the output of the script. Then wait for the user's reply. Only continue if the reply is exactly the 8 digits, nothing else.

## Why the script path works

`~/.claude/skills/generate-8digit-code/` is a symlink (created by `ccst skills install`) into the installed `cc-session-tools` source tree, so the script path resolves correctly regardless of where the package was installed.
