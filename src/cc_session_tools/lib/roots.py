from pathlib import Path

DEFAULT_ROOTS_FILE = Path.home() / ".claude" / "cc-session-roots.txt"


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
