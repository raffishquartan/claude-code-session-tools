from pathlib import Path


def default_roots_file() -> Path:
    return Path.home() / ".claude" / "cc-session-roots.txt"


def strict_root_link() -> Path:
    return Path.home() / "cc-claude-code"


# Backwards-compatible aliases so callers that imported these as constants
# still work, but evaluate at call time. `DEFAULT_ROOTS_FILE` is captured at
# module-import time by older code; new code should call `default_roots_file()`.
DEFAULT_ROOTS_FILE = default_roots_file()
STRICT_ROOT_LINK = strict_root_link()


def load_session_roots(roots_file: Path | None = None) -> list[Path]:
    if roots_file is None:
        roots_file = default_roots_file()
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
        return strict_root_link().resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None


def is_strict_root(root: Path) -> bool:
    sr = strict_root_path()
    return sr is not None and root == sr
