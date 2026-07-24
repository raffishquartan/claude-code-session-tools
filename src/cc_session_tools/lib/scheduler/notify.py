"""Best-effort Telegram push for events a headless scheduler worker needs to
surface even when no Claude Code session is open to read the digest. Talks to
the Telegram Bot API directly over HTTPS — the same mechanism the interactive
`notify-user` skill uses, reproduced here because a detached `ccsched _run-job`
subprocess has no LLM in the loop to invoke a skill through.

Credentials come from the environment (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
if already exported, else are parsed directly from ``~/.creds`` (override via
``CCCS_CREDS_PATH``) since a detached subprocess's inherited environment is not
guaranteed to have sourced a shell profile. Every failure mode degrades to a
logged warning and a ``False`` return — a notification that can't be sent must
never take down the worker it's reporting on."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_CREDS_PATH_ENV = "CCCS_CREDS_PATH"
_API_BASE = "https://api.telegram.org"

Poster = Callable[[str, bytes], None]


def _creds_path() -> Path:
    raw = os.environ.get(_CREDS_PATH_ENV)
    return Path(raw).expanduser() if raw else Path.home() / ".creds"


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ").strip()
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _credentials() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        parsed = _parse_env_file(_creds_path())
        token = token or parsed.get("TELEGRAM_BOT_TOKEN")
        chat_id = chat_id or parsed.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    return token, chat_id


def _default_post(url: str, data: bytes) -> None:
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    urllib.request.urlopen(req, timeout=10)  # noqa: S310 -- fixed https host, not user input


def send_telegram(message: str, *, post: Poster = _default_post) -> bool:
    """Best-effort send. Returns False (and logs) on missing credentials or any
    transport failure — never raises, so a broken notification path can't crash
    the scheduler worker it's meant to be reporting a failure from."""
    creds = _credentials()
    if creds is None:
        logger.warning(
            "telegram notify skipped: no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
            "in env or %s", _creds_path(),
        )
        return False
    token, chat_id = creds
    url = f"{_API_BASE}/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
    try:
        post(url, payload)
    except (OSError, ValueError) as exc:
        logger.warning("telegram notify failed: %s", exc)
        return False
    return True


def suspended(job_id: str, consecutive_failures: int, *, post: Poster = _default_post) -> bool:
    """The one-time push fired when a job crosses the auto-suspend threshold."""
    message = (
        f"[cc-scheduler] {job_id} auto-suspended after {consecutive_failures} "
        f"consecutive failures — see `ccsched status {job_id}` / run "
        f"`ccsched enable {job_id}` after fixing"
    )
    return send_telegram(message, post=post)
