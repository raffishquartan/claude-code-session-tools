from pathlib import Path

DEFAULT_ROOTS_FILE = Path.home() / ".claude" / "cc-session-roots.txt"

# Pinned by symlink path; the actual filesystem location is whatever
# realpath resolves it to, so the strict-root identity follows the symlink
# target.
STRICT_ROOT_LINK = Path.home() / "cc-claude-code"


def load_session_roots(roots_file: Path = DEFAULT_ROOTS_FILE) -> list[Path]:
    out: list[Path] = []
    for raw in roots_file.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        token = line.split()[0]
        p = Path(token).expanduser()
        try:
            real = p.resolve(strict=True)
        except (FileNotFoundError, OSError):
            continue
        if real.is_dir():
            out.append(real)
    return out


def matched_session_root(cwd_abs: Path, roots: list[Path]) -> Path | None:
    parent = cwd_abs.parent
    for r in roots:
        if parent == r:
            return r
    return None


def is_valid_session_cwd(cwd_abs: Path, roots: list[Path]) -> bool:
    return matched_session_root(cwd_abs, roots) is not None


def strict_root_path() -> Path | None:
    try:
        return STRICT_ROOT_LINK.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None


def is_strict_root(root: Path) -> bool:
    sr = strict_root_path()
    return sr is not None and root == sr
