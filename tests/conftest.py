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
    tests. Tests that need roots set must do so explicitly.

    CCST_NO_AUTO_SYNC=1 is set for the whole suite, not cleared: ccst's
    main() auto-applies `install-everything` when the installed version
    doesn't match the recorded sync marker, and dozens of tests reach main()
    either in-process or through `subprocess.run([sys.executable, "-m",
    "cc_session_tools.cli.ccst", ...])`. monkeypatch.setenv mutates
    os.environ, which those subprocesses inherit, so this one fixture covers
    both groups. Tests that exercise auto-sync on purpose delete the var
    themselves after redirecting HOME, CCST_DATA_HOME and CCST_SESSIONS_DIR
    at tmp_path.
    """
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    monkeypatch.delenv("CCX_DEBUG", raising=False)
    monkeypatch.setenv("CCST_NO_AUTO_SYNC", "1")
