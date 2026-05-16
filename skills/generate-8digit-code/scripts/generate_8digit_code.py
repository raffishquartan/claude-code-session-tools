#!/usr/bin/env python3
"""Print a cryptographically random 8-digit confirmation code to stdout.

Always invoke this script when a Claude Code session needs to generate an
8-digit confirmation code for a gated action. Never let the model make up a
number — LLMs are not random number generators.

Output: exactly one 8-digit zero-padded decimal string followed by a newline.
Exit code: 0 on success, 1 on error (message on stderr).

Usage:
    python3 ~/.claude/skills/generate-8digit-code/scripts/generate_8digit_code.py
"""
import secrets
import sys


def generate() -> str:
    """Return a cryptographically random 8-digit zero-padded decimal string."""
    return f"{secrets.randbelow(100_000_000):08d}"


if __name__ == "__main__":
    try:
        sys.stdout.write(generate() + "\n")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"generate_8digit_code: error: {exc}\n")
        sys.exit(1)
