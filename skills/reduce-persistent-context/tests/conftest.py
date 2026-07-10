"""Shared pytest bootstrap for reduce-persistent-context tests.

Puts scripts/ on sys.path so the bare sibling imports used throughout this
skill (`import measure`, `from tokens import token_count`, ...) resolve,
mirroring the pattern in skills/move-session/tests/conftest.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"

# Repo src/ first (already on pythonpath via pyproject.toml when run from
# repo root, but this keeps the conftest self-sufficient if pytest is ever
# invoked directly from inside this skill's own tests/ dir).
_REPO_SRC = str(Path(__file__).resolve().parents[3] / "src")
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, _REPO_SRC)
