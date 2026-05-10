"""UserPromptSubmit hook: scan for credential shapes and prompt-injection patterns.

Warns to stderr but never blocks (exit 0 always). False positive risk is too
high to block; a warning is sufficient to surface the issue to the user.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys

_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key ID", re.compile(r'AKIA[0-9A-Z]{16}')),
    ("AWS secret key (heuristic)", re.compile(r'(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+])')),
    ("GitHub PAT", re.compile(r'ghp_[A-Za-z0-9]{36}')),
    ("JWT token", re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')),
]

_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("prompt injection: ignore instructions", re.compile(
        r'ignore\s+(all\s+)?previous\s+instructions', re.IGNORECASE
    )),
    ("prompt injection: developer mode", re.compile(
        r'(you are now|switch to|enable)\s+(in\s+)?developer mode', re.IGNORECASE
    )),
    ("prompt injection: disregard", re.compile(
        r'disregard\s+(your\s+)?(previous|prior|above|all)\s+(instructions|rules|constraints)',
        re.IGNORECASE,
    )),
    ("prompt injection: new persona", re.compile(
        r'you are now\s+\w+', re.IGNORECASE
    )),
]


@dataclasses.dataclass(frozen=True, slots=True)
class GuardFindings:
    warnings: list[str]


def scan_prompt(text: str) -> GuardFindings:
    warnings: list[str] = []

    for name, pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(text):
            warnings.append(f"⚠ [prompt-guard] Possible credential in prompt ({name})")

    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            warnings.append(f"⚠ [prompt-guard] Possible prompt injection pattern ({name})")

    return GuardFindings(warnings=warnings)


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    prompt = str(data.get("prompt", ""))
    if not prompt:
        return 0

    findings = scan_prompt(prompt)
    for warning in findings.warnings:
        print(warning, file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
