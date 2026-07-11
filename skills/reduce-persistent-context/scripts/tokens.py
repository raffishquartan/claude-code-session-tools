"""Single tokenizer call site. Shells out to tiktoken-tools' count-text-tokens.

Real output is multi-line and lists Characters BEFORE Tokens, e.g.:
    File: <stdin>
    Model (for tokenization): gpt-4o-mini
    Characters: 11
    Words (whitespace split): 2
    Tokens: 2
    Tokens/word: 1.000
So we must parse the "Tokens:" line specifically, NOT the first integer.
"""
import re
import subprocess


def _run(text: str) -> str:
    return subprocess.run(
        ["count-text-tokens", "-f", "-"],
        input=text, capture_output=True, text=True, check=True,
    ).stdout


def token_count(text: str) -> int:
    if text == "":
        return 0
    stdout = _run(text)
    # Match a line that is exactly "Tokens: <int>" (not "Tokens/word:").
    m = re.search(r"^Tokens:\s*([\d,]+)\s*$", stdout, re.MULTILINE)
    if not m:
        raise ValueError(f"could not parse 'Tokens:' line from: {stdout!r}")
    return int(m.group(1).replace(",", ""))
