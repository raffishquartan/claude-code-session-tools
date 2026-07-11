"""Shared pytest bootstrap for reduce-persistent-context tests.

Puts scripts/ on sys.path so the bare sibling imports used throughout this
skill (`import measure`, `from tokens import token_count`, ...) resolve,
mirroring the pattern in skills/move-session/tests/conftest.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"

# Repo src/ first (already on pythonpath via pyproject.toml when run from
# repo root, but this keeps the conftest self-sufficient if pytest is ever
# invoked directly from inside this skill's own tests/ dir).
_REPO_SRC = str(Path(__file__).resolve().parents[3] / "src")
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, _REPO_SRC)

import tokens  # noqa: E402


@pytest.fixture(autouse=True)
def fake_count_text_tokens(monkeypatch):
    """Stub tokens._run so tests don't need the real count-text-tokens binary.

    count-text-tokens ships from tiktoken-tools, a personal pipx-installed
    tool that isn't published to PyPI - it exists on the maintainer's machine
    but not in CI or any other contributor's checkout. The fake reproduces
    count-text-tokens' real stdout shape (whitespace word count as the token
    count) so tokens.token_count's "Tokens:" line parser is still exercised.
    """
    def fake_run(text: str) -> str:
        word_count = len(text.split())
        return (
            "File: <stdin>\n"
            "Model (for tokenization): gpt-4o-mini\n"
            f"Characters: {len(text)}\n"
            f"Words (whitespace split): {word_count}\n"
            f"Tokens: {word_count}\n"
            "Tokens/word: 1.000\n"
        )

    monkeypatch.setattr(tokens, "_run", fake_run)
