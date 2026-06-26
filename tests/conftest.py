import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def tmp_hooks_dir(tmp_path: Path) -> Path:
    """Temp directory standing in for ~/.cache/claude/logs/."""
    d = tmp_path / "hooks"
    d.mkdir(mode=0o700)
    return d


@pytest.fixture(autouse=True)
def _clean_session_root_env(monkeypatch):
    """Make sure no inherited env vars from the developer's shell leak into
    tests. Tests that need roots set must do so explicitly."""
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    monkeypatch.delenv("CCX_DEBUG", raising=False)
