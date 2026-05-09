"""Claude Code session management CLI tools (ccd, ccr, ccs)."""

from __future__ import annotations

__all__ = ["__version__"]


def _get_version() -> str:
    # Prefer the installed-distribution version (set by pipx/pip install).
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("cc-session-tools")
    except PackageNotFoundError:
        pass
    except ImportError:
        pass
    # Fallback for source-tree runs (e.g. PYTHONPATH=src python -m ...).
    return "0.0.0+source"


__version__ = _get_version()
