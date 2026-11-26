"""Shared fixtures for the self-improvement loop tests.

Every test runs against a temporary HOME, data home, ccmsg root and telemetry directory, so
nothing touches the real state directory, backlog, transcripts or stores. The Telegram sender
is replaced by a recorder; no test can reach the network or a real model.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import pytest


def days_ago(n: int) -> str:
    """UTC calendar date N days before today, yyyy-MM-dd."""
    return (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=n)).isoformat()


def iso_days_ago(n: int) -> str:
    """Noon UTC N days ago, the shape reviews.log lines use."""
    return f"{days_ago(n)}T12:00:00Z"


@dataclass
class Notifier:
    messages: list[str] = field(default_factory=list)
    fail: bool = False

    def __call__(self, message: str) -> bool:
        self.messages.append(message)
        return not self.fail


@dataclass
class Env:
    root: Path
    home: Path
    state: Path
    stores: Path
    notify: Notifier

    @property
    def backlog(self) -> Path:
        return self.home / ".claude" / "self-improvement-backlog.md"

    @property
    def projects(self) -> Path:
        return self.home / ".claude" / "projects"

    def install_review_skill(self) -> None:
        """An installed interactive review skill, so messages name its periodic command."""
        skill = self.home / ".claude" / "skills" / "review-session-and-improve-claude-setup"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("---\nname: review-session-and-improve-claude-setup\n---\n")

    def review_line(self, text: str) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "reviews.log").open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    def interactive_review(self, age_days: int, mode: str = "C") -> None:
        self.review_line(f"{iso_days_ago(age_days)} mode={mode} findings=0 applied=0 deferred=0")

    def last_log(self) -> str:
        return (self.state / "reviews.log").read_text(encoding="utf-8", errors="replace").splitlines()[-1]

    def snapshot_value(self, key: str) -> str:
        for line in (self.state / "signals.env").read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        raise KeyError(key)

    def write_backlog(self, inbox: int, oldest: int = 0, open_entries: int = 0) -> None:
        """A backlog with ``inbox`` Inbox lines, the oldest dated ``oldest`` days ago, plus
        ``open_entries`` open entries and one applied entry."""
        lines = [
            "# Self-improvement backlog", "", "## Inbox",
            "<!-- - [YYYY-MM-DD] [session-tag] one-line friction observation -->",
        ]
        lines += [f"- [{days_ago(oldest - i)}] [tag-{i}] friction note {i}" for i in range(inbox)]
        lines += ["", "## Entries", "<!-- ### [B-001] title", "- status: open", "-->", ""]
        for i in range(open_entries):
            lines += [f"### [B-{i:03d}] open item", "- lever: skill", "- status: open", ""]
        lines += ["### [B-900] done item", "- lever: skill", "- status: applied"]
        self.backlog.parent.mkdir(parents=True, exist_ok=True)
        self.backlog.write_text("\n".join(lines) + "\n", encoding="utf-8")


def tree_checksums(root: Path, exclude: list[Path]) -> dict[str, bytes]:
    """Content of every file under ``root`` except those under an excluded path."""
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(path == e or e in path.parents for e in exclude):
            continue
        out[str(path.relative_to(root))] = path.read_bytes()
    return out


@pytest.fixture
def si(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    home = tmp_path / "home"
    data_home = home / ".local" / "share" / "claude"
    stores = tmp_path / "stores"
    for d in (home / ".claude", data_home, stores):
        d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_DATA_HOME", str(data_home))
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(stores))
    monkeypatch.setenv("CCST_HOOKS_DIR", str(stores))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.delenv("SELF_IMPROVEMENT_NO_ESCALATE", raising=False)
    monkeypatch.delenv("SELF_IMPROVEMENT_MODEL_TIMEOUT_S", raising=False)
    monkeypatch.delenv("SELF_IMPROVEMENT_JOB_BUDGET_S", raising=False)
    return Env(
        root=tmp_path, home=home, state=data_home / "self-improvement", stores=stores,
        notify=Notifier(),
    )
