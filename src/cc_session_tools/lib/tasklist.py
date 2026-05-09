from pathlib import Path

from .roots import load_session_roots


def id_for_project(project_dir: Path, roots: list[Path] | None = None) -> str | None:
    if roots is None:
        roots = load_session_roots()
    try:
        resolved = project_dir.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None
    parent = resolved.parent
    for root in roots:
        if parent == root:
            return resolved.name
    return None
