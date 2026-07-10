"""Find duplicate skill pairs and choose the canonical member."""
from dataclasses import dataclass
from pathlib import Path
import re

KNOWN_PAIRS = [
    ("count-tokens", "count-file-tokens"),
    ("open-browser-tab", "opening-browser-tabs"),
    ("solve-captcha", "solving-captchas"),
    ("auth-our-family-wizard", "our-family-wizard-authenticate"),
    ("expenses-reconciliation", "maxella-expenses-reconciliation"),
    ("journal-search", "search-journal"),
    ("travel-booking-to-calendar", "travel-booking-from-email-to-personal-calendar"),
]
SIMILARITY_THRESHOLD = 0.6


@dataclass(frozen=True)
class DuplicatePair:
    canonical: str
    redundant: str
    reason: str


def _desc(skill_md: Path) -> str:
    m = re.search(r"description:\s*\|?\s*(.+?)(?:\n\w+:|\n---)",
                  skill_md.read_text(), re.DOTALL)
    return (m.group(1) if m else "").lower()


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _pick_canonical(p: Path, a: str, b: str) -> tuple[str, str]:
    size_a = (p / a / "SKILL.md").stat().st_size
    size_b = (p / b / "SKILL.md").stat().st_size
    if size_a != size_b:
        return (a, b) if size_a > size_b else (b, a)
    return (a, b) if len(a) <= len(b) else (b, a)


def find_duplicate_pairs(skills_dir: Path) -> list[DuplicatePair]:
    present = {d.name for d in skills_dir.iterdir()
              if (d / "SKILL.md").exists()}
    pairs: list[DuplicatePair] = []
    seen: set[frozenset[str]] = set()

    def add(a: str, b: str, reason: str) -> None:
        key = frozenset((a, b))
        if key in seen:
            return
        seen.add(key)
        canon, redun = _pick_canonical(skills_dir, a, b)
        pairs.append(DuplicatePair(canon, redun, reason))

    for a, b in KNOWN_PAIRS:
        if a in present and b in present:
            add(a, b, "known pair")

    names = sorted(present)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if frozenset((a, b)) in seen:
                continue
            if _jaccard(_desc(skills_dir / a / "SKILL.md"),
                        _desc(skills_dir / b / "SKILL.md")) >= SIMILARITY_THRESHOLD:
                add(a, b, "description similarity")
    return pairs
