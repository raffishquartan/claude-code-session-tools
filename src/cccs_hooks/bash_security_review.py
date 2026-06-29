"""PreToolUse hook: tiered security review with command cache.

Tiers:
  0.  Trivial allowlist (ls, pwd, git status, ...) - exit silently.
  0.5 Read-only pre-filter - nontrivial commands with no heuristic flags and
      no write/network/exec risk patterns - exit silently. Eliminates LLM
      calls for piped read-only commands like `grep foo | wc -l`.
  1.  Heuristic-flagged (pipe-to-shell, eval, base64 -d, ...) - always claude,
      never cache.
  2.  Cache hit (CCCS_USE_COMMAND_CACHE=1, fresh entry) - emit cached verdict.
  3.  Cache miss / disabled / stale - call claude CLI; on `safe` verdict and
      no heuristic flags, record in cache.

Never blocks. On any error/timeout, prints "[security review unavailable: ...]"
and exits 0. Telemetry is always written, regardless of tier.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from cccs_hooks import cache as cache_mod
from cccs_hooks import normalise as norm_mod
from cccs_hooks.telemetry import TelemetryEntry, log_event, _shorten_cwd


_TRIVIAL_RE = re.compile(
    r"^\s*(ls|pwd|cat|head|tail|wc|which|stat|file|basename|echo|printf|"
    r"date|env|jq|bats|pytest|node|npm|python3?|pip3?)(\s|$)"
)
_GIT_TRIVIAL_RE = re.compile(
    r"^\s*git\s+(status|diff|log|show|branch|rev-parse|config)(\s|$)"
)
_NONTRIVIAL_RE = re.compile(r"[|;<]|&&|\|\||\$\(|`|<\(")

# Heuristic patterns from existing bash hook (lines 58-69).
_HEURISTIC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\|\s*(sh|bash|zsh)(\s|$)"), "pipe to shell"),
    (re.compile(r"(^|\s)eval(\s|$)"), "eval"),
    (re.compile(r"base64\s+(-d|--decode)"), "base64 decode"),
    (re.compile(r"(\.ssh|id_rsa|id_ed25519|\.aws/credentials|\.netrc)"), "credentials path"),
    (re.compile(r"(printenv|env\s*$|env\s*\|)"), "env dump"),
    (re.compile(r"chmod\s+\S*s"), "setuid chmod"),
    (re.compile(r"(^|\s)(/etc/|/usr/|/var/|/boot/)"), "system path"),
    (re.compile(r"(nc|ncat|netcat|socat)\s"), "raw network tool"),
    (re.compile(r"(wget|curl).*-O\s*/"), "download to absolute path"),
]

_VERDICT_RE = re.compile(r"^VERDICT:\s*(safe|suspicious|dangerous)\s*$", re.MULTILINE)

# Write-risk patterns: if any match, the command warrants LLM review even when
# it has no heuristic flags. Uses a blocklist rather than an allowlist so it
# stays robust as new safe commands are added to sessions.
#
# Redirection note: `[0-9&]?>>?` matches `>`, `>>`, `2>`, `&>` etc.
# Negative lookaheads exclude fd-merges (`>&N`) and `/dev/null` suppression,
# which are harmless and appear in nearly every session.
_WRITE_RISK_RE = re.compile(
    r"""(?x)
    # Output redirection to a real destination (not >&N or /dev/null)
    [0-9&]?>>?\s*(?!/dev/null)(?!&\d)
    # File-write / destructive operations
    | \b(tee|dd|truncate|shred|rm|rmdir|unlink|mv|cp|chmod|chown|chgrp)\b
    # Network operations (could exfiltrate or download)
    | \b(curl|wget|ssh|scp|rsync|sftp|ftp)\b
    # Privilege escalation
    | \bsudo\b
    # Package management install/remove
    | \b(?:apt|apt-get|yum|dnf|brew)\s+(?:install|remove|purge|uninstall|update|upgrade)\b
    | \bpip3?\s+(?:install|uninstall|remove)\b
    | \bnpm\s+(?:install|uninstall|publish|update|ci)\b
    # System service control
    | \b(?:systemctl|service)\b
    # Cron modification
    | \bcrontab\s
    # Git write operations (modifies working tree, history, or remote)
    | \bgit\s+(?:push|commit|clean\b|reset|rebase|merge|fetch|stash|checkout|cherry-pick|am|apply)
    """,
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True, slots=True)
class HookInput:
    tool_name: str
    command: str
    session_id: str
    cwd: str
    raw: dict[str, object]


def parse_input(raw: str) -> HookInput | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tool_name = str(data.get("tool_name", ""))
    tool_input = data.get("tool_input") or {}
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command", ""))
    return HookInput(
        tool_name=tool_name,
        command=command,
        session_id=str(data.get("session_id", "")),
        cwd=str(data.get("cwd", "")),
        raw=data,
    )


def is_trivial(command: str) -> bool:
    """True if the command is on the trivial allowlist with no shell composition."""
    if not (_TRIVIAL_RE.match(command) or _GIT_TRIVIAL_RE.match(command)):
        return False
    if _NONTRIVIAL_RE.search(command):
        return False
    if len(command) >= 120:
        return False
    return True


def heuristic_flags(command: str) -> list[str]:
    hits: list[str] = []
    for pattern, label in _HEURISTIC_PATTERNS:
        if pattern.search(command):
            hits.append(label)
    return hits


def has_write_risk(command: str) -> bool:
    """True if *command* contains write, network, or privilege-escalation risk patterns."""
    return bool(_WRITE_RISK_RE.search(command))


def session_prefix(cwd: str) -> str:
    """Return '<session-name>: ' prefix if cwd has exactly one matching cc-sessions subdir."""
    if not cwd:
        return ""
    sess_dir = Path(cwd) / "cc-sessions"
    if not sess_dir.is_dir():
        return ""
    pattern = re.compile(r"^[0-9]{8}(-to-[0-9]{8})?-[^/]+$")
    matches = [
        p for p in sess_dir.iterdir() if p.is_dir() and pattern.match(p.name)
    ]
    if len(matches) != 1:
        return ""
    return f"{matches[0].name}: "


def build_prompt(command: str, cwd: str) -> str:
    prefix = session_prefix(cwd)
    return (
        f"{prefix}Review this shell command for security risks and side effects. "
        f"Reply in this EXACT format, no preamble:\n"
        f"SUMMARY: <one-line plain-language description of what it does>\n"
        f"RISKS: <'none' or a short comma-separated list>\n"
        f"VERDICT: <safe|suspicious|dangerous>\n\n"
        f"Command:\n{command}"
    )


def extract_verdict(review_text: str) -> str:
    m = _VERDICT_RE.search(review_text)
    if m:
        return m.group(1)
    return "unknown"


def format_review_for_stderr(review_text: str, hits: list[str]) -> str:
    """Convert SUMMARY/RISKS/VERDICT lines into the human-readable form."""
    out_lines = ["[security review]"]
    for line in review_text.splitlines():
        if line.startswith("SUMMARY:"):
            out_lines.append("What it does:" + line[len("SUMMARY:") :])
        elif line.startswith("RISKS:"):
            out_lines.append("Risks:" + line[len("RISKS:") :])
        elif line.startswith("VERDICT:"):
            out_lines.append("Verdict:" + line[len("VERDICT:") :])
        else:
            out_lines.append(line)
    if hits:
        out_lines.append(f"Heuristic flags: {'; '.join(hits)}")
    return "\n".join(out_lines)


def call_claude(prompt: str, *, claude_bin: str, timeout: int) -> tuple[str | None, str | None]:
    """Run claude CLI. Return (review_text, error_message). Exactly one is non-None."""
    _env = os.environ.copy()
    # Give the sub-process its own distinct session identity so its SessionStart
    # hook writes a .tag file that cannot collide with the parent session's tag.
    # CLD_SESSION_DIR is intentionally kept: the sub-process belongs to the same
    # session directory and its .last-opened write is harmless and correct.
    _env["CLD_SESSION_TAG"] = f"bash-security-review-{time.strftime('%Y%m%d-%H%M')}"
    _env["CLD_SESSION_MODE"] = "hook"
    try:
        result = subprocess.run(
            [claude_bin, "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env,
        )
    except FileNotFoundError:
        return None, "claude CLI not found"
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout}s"
    except OSError as e:
        return None, f"claude exec failed: {e}"
    if result.returncode != 0:
        return None, f"claude exited {result.returncode}"
    text = result.stdout.strip()
    if not text:
        return None, "empty review"
    return text, None


def _resolve_claude_bin() -> str | None:
    env_bin = os.environ.get("CCCS_CLAUDE_BIN", "").strip()
    if env_bin:
        if Path(env_bin).is_file() and os.access(env_bin, os.X_OK):
            return env_bin
        return None
    # Look up on PATH.
    import shutil
    return shutil.which("claude")


def _emit_telemetry(
    *, hi: HookInput, decision: str, cache_state: str, verdict: str, sha: str
) -> None:
    hooks_dir_env = os.environ.get("CCCS_HOOKS_DIR")
    hooks_dir = Path(hooks_dir_env) if hooks_dir_env else None
    entry = TelemetryEntry(
        hook="bash-security-review",
        event="PreToolUse",
        tool=hi.tool_name,
        session_id=hi.session_id,
        cwd_short=_shorten_cwd(hi.cwd),
        decision=decision,  # type: ignore[arg-type]
        cache=cache_state,  # type: ignore[arg-type]
        verdict=verdict,
        input_hash=f"sha256:{sha}",
    )
    log_event(entry, hooks_dir=hooks_dir)


def _command_preview(command: str, limit: int = 200) -> str:
    if len(command) <= limit:
        return command
    return command[:limit] + "..."


def run(stdin_text: str) -> int:
    hi = parse_input(stdin_text)
    if hi is None:
        return 0
    if hi.tool_name != "Bash" or not hi.command:
        return 0

    command = hi.command
    sha = cache_mod.sha256_command(command)

    # ---- Tier 0: trivial allowlist ----
    if is_trivial(command):
        _emit_telemetry(
            hi=hi, decision="allow", cache_state="none", verdict="trivial", sha=sha
        )
        cache_mod.invocations_record(
            exit_tier=0,
            verdict="allow",
            session_id=hi.session_id or None,
            tool_name=hi.tool_name,
            exact_hash=sha,
        )
        return 0

    hits = heuristic_flags(command)
    use_cache = os.environ.get("CCCS_USE_COMMAND_CACHE", "") == "1"

    # ---- Tier 1: heuristic hit -> always escalate, never cache ----
    skip_cache = bool(hits)
    norm_form = norm_mod.normalise(command) if not skip_cache else None
    norm_sha  = cache_mod.sha256_command(norm_form) if norm_form else None

    # Only "non-trivial" commands continue past this gate to claude. The bash
    # original short-circuits when the command is borderline. Mirror that:
    nontrivial = (
        bool(hits)
        or _NONTRIVIAL_RE.search(command) is not None
        or len(command) > 120
    )
    if not nontrivial:
        _emit_telemetry(
            hi=hi, decision="allow", cache_state="none", verdict="trivial", sha=sha
        )
        cache_mod.invocations_record(
            exit_tier=0,
            verdict="allow",
            session_id=hi.session_id or None,
            tool_name=hi.tool_name,
            exact_hash=sha,
        )
        return 0

    # ---- Tier 0.5: read-only pre-filter ----
    # At this point the command is nontrivial (has shell composition, heuristic
    # flags, or exceeds the length threshold). If there are no heuristic flags
    # and no write/network/exec risk patterns, the command is safe to skip
    # regardless of shell composition — piped read-only chains like
    # `grep foo | wc -l` or `git log | head -20` carry no meaningful risk.
    if not hits and not has_write_risk(command):
        _emit_telemetry(
            hi=hi, decision="allow", cache_state="none", verdict="read-only", sha=sha
        )
        cache_mod.invocations_record(
            exit_tier=0,
            verdict="allow",
            session_id=hi.session_id or None,
            tool_name=hi.tool_name,
            exact_hash=sha,
        )
        return 0

    # ---- Tier 2: cache lookup ----
    if use_cache and not skip_cache:
        entry = cache_mod.cache_lookup(sha, norm_sha=norm_sha)
        if entry is not None:
            # cache_lookup already filters stale entries internally
            review = (
                f"[security review]\n"
                f"What it does: {entry.command_preview}\n"
                f"Risks: {entry.risks_summary}\n"
                f"Verdict: {entry.verdict}\n"
                f"(cached, source={entry.cache_source}, fires={entry.fire_count})"
            )
            sys.stderr.write(review + "\n")
            _emit_telemetry(
                hi=hi,
                decision="allow",
                cache_state="hit",
                verdict=entry.verdict,
                sha=sha,
            )
            cache_source = "exact" if entry.exact_hash == sha else "norm"
            cache_mod.invocations_record(
                exit_tier=2,
                verdict=entry.verdict,
                session_id=hi.session_id or None,
                tool_name=hi.tool_name,
                cache_source=cache_source,
                exact_hash=sha,
            )
            return 0

    # ---- Tier 3: claude escalation ----
    claude_bin = _resolve_claude_bin()
    if claude_bin is None:
        sys.stderr.write("[security review unavailable: claude CLI not found]\n")
        _emit_telemetry(
            hi=hi,
            decision="allow",
            cache_state=("miss" if use_cache and not skip_cache else "none"),
            verdict="unavailable",
            sha=sha,
        )
        return 0

    try:
        timeout = int(os.environ.get("CCCS_REVIEW_TIMEOUT", "30"))
    except ValueError:
        timeout = 30

    prompt = build_prompt(command, hi.cwd)
    _t0 = time.monotonic()
    review_text, err = call_claude(prompt, claude_bin=claude_bin, timeout=timeout)
    _ms_elapsed = int((time.monotonic() - _t0) * 1000)
    if err is not None:
        sys.stderr.write(f"[security review unavailable: {err}]\n")
        _emit_telemetry(
            hi=hi,
            decision="allow",
            cache_state=("miss" if use_cache and not skip_cache else "none"),
            verdict="unavailable",
            sha=sha,
        )
        cache_mod.invocations_record(
            exit_tier=3,
            verdict="unavailable",
            session_id=hi.session_id,
            tool_name=hi.tool_name,
            heuristic_fired=bool(hits),
            heuristic_names=list(hits) if hits else None,
            exact_hash=sha,
            ms_elapsed=_ms_elapsed,
        )
        return 0

    assert review_text is not None
    sys.stderr.write(format_review_for_stderr(review_text, hits) + "\n")
    verdict = extract_verdict(review_text)

    cache_state = "miss" if use_cache and not skip_cache else "none"
    if use_cache and not skip_cache and verdict == "safe":
        cache_mod.cache_record(
            sha,
            verdict,
            risks_summary=_extract_field(review_text, "RISKS") or "none",
            command_preview=_command_preview(command),
            norm_sha=norm_sha,
        )

    _emit_telemetry(
        hi=hi,
        decision="allow",
        cache_state=cache_state,
        verdict=verdict,
        sha=sha,
    )
    cache_mod.invocations_record(
        exit_tier=3,
        verdict=verdict,
        session_id=hi.session_id or None,
        tool_name=hi.tool_name,
        heuristic_fired=bool(hits),
        heuristic_names=list(hits) if hits else None,
        exact_hash=sha,
        ms_elapsed=_ms_elapsed,
    )
    return 0


def _extract_field(review_text: str, field: str) -> str | None:
    pattern = re.compile(rf"^{field}:\s*(.+)$", re.MULTILINE)
    m = pattern.search(review_text)
    return m.group(1).strip() if m else None


def main() -> int:
    stdin_text = sys.stdin.read()
    return run(stdin_text)


if __name__ == "__main__":
    sys.exit(main())
