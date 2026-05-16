#!/usr/bin/env python3
"""Print a cryptographically random 8-digit confirmation code to stdout.

Used by the delete-sessions skill and any other skill that needs an 8-digit
gate, so the code is genuinely random rather than AI-generated.

Usage:
    python3 ~/.claude/skills/<skill>/scripts/generate_8digit_code.py
    # or via the installed CLI:
    python3 ~/repos/claude-code-session-tools/scripts/generate_8digit_code.py
"""
import secrets
import sys


def generate() -> str:
    return f"{secrets.randbelow(100_000_000):08d}"


if __name__ == "__main__":
    sys.stdout.write(generate() + "\n")
