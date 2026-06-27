"""Health-check logic for ``ccst doctor``.

Runs a suite of checks and returns a list of :class:`CheckResult` objects.
Each check has a status (OK / WARN / FAIL), a name, and a reason string.

The module is intentionally pure: no I/O side effects, all filesystem paths
are passed in (makes unit testing straightforward).
"""
from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class Status(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    reason: str

    def __str__(self) -> str:
        return f"[{self.status.value:<4}] {self.name}: {self.reason}"


# ---------- individual checks ----------


def check_cli_on_path(cli_name: str) -> CheckResult:
    """Verify the named CLI is on PATH and reports a version."""
    if shutil.which(cli_name) is None:
        return CheckResult(
            name=f"PATH:{cli_name}",
            status=Status.FAIL,
            reason=f"{cli_name!r} not found on PATH",
        )
    try:
        result = subprocess.run(
            [cli_name, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return CheckResult(
                name=f"PATH:{cli_name}",
                status=Status.WARN,
                reason=f"{cli_name} --version exited {result.returncode}",
            )
        version_out = (result.stdout + result.stderr).strip()
        return CheckResult(
            name=f"PATH:{cli_name}",
            status=Status.OK,
            reason=version_out.split("\n")[0],
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as e:
        return CheckResult(
            name=f"PATH:{cli_name}",
            status=Status.WARN,
            reason=f"could not run {cli_name} --version: {e}",
        )


def check_env_dir(var_name: str, env_value: str | None) -> CheckResult:
    """Check that an env var is set and points to an existing directory."""
    name = f"ENV:{var_name}"
    if env_value is None:
        return CheckResult(name=name, status=Status.WARN, reason="not set")
    p = Path(env_value)
    if not p.is_dir():
        return CheckResult(
            name=name,
            status=Status.FAIL,
            reason=f"set to {env_value!r} but directory does not exist",
        )
    return CheckResult(name=name, status=Status.OK, reason=str(p))


def check_settings_json(settings_path: Path) -> CheckResult:
    """Verify settings.json exists and is valid JSON."""
    name = "settings.json"
    if not settings_path.exists():
        return CheckResult(name=name, status=Status.FAIL, reason=f"not found: {settings_path}")
    try:
        with settings_path.open() as f:
            json.load(f)
        return CheckResult(name=name, status=Status.OK, reason=str(settings_path))
    except json.JSONDecodeError as e:
        return CheckResult(name=name, status=Status.FAIL, reason=f"invalid JSON: {e}")
    except OSError as e:
        return CheckResult(name=name, status=Status.FAIL, reason=f"cannot read: {e}")


def _hook_command_present(settings: dict[str, Any], command: str) -> bool:
    """Return True if ``command`` appears in any hooks block in settings."""
    hooks_section = settings.get("hooks", {})
    for _event, blocks in hooks_section.items():
        for block in blocks:
            for hook_entry in block.get("hooks", []):
                if hook_entry.get("command") == command:
                    return True
    return False


def check_hook_registered(
    hook_name: str,
    settings: dict[str, Any],
) -> CheckResult:
    """Check that ccst hooks run <hook_name> is present in settings."""
    command = f"ccst hooks run {hook_name}"
    name = f"hook:{hook_name}"
    if _hook_command_present(settings, command):
        return CheckResult(name=name, status=Status.OK, reason="registered")
    return CheckResult(
        name=name,
        status=Status.WARN,
        reason=f"{command!r} not found in settings.json",
    )


def check_skill_symlink(skill_name: str, skill_src: Path, skills_dir: Path) -> CheckResult:
    """Check that skills_dir/<skill_name> is a valid CCST skill symlink.

    A symlink is OK if:
      - it resolves to ``skill_src`` exactly (the currently invoked ccst's
        bundled source), OR
      - it resolves to *any* directory named ``<skill_name>`` that contains a
        SKILL.md file — i.e. a different but otherwise valid CCST install
        (canonical clone vs worktree, multiple clones, pipx vs uv tool dir).

    The second case is reported as OK with a parenthetical NOTE so the user
    can spot the divergence but doctor does not FAIL on what is a legitimate
    multi-install setup.
    """
    name = f"skill:{skill_name}"
    dest = skills_dir / skill_name
    if not dest.exists() and not dest.is_symlink():
        return CheckResult(
            name=name,
            status=Status.WARN,
            reason=f"no symlink at {dest}",
        )
    if not dest.is_symlink():
        return CheckResult(
            name=name,
            status=Status.FAIL,
            reason=f"{dest} exists but is not a symlink",
        )
    actual = dest.resolve()
    expected = skill_src.resolve()
    if actual == expected:
        return CheckResult(name=name, status=Status.OK, reason=f"-> {actual}")
    if actual.is_dir() and actual.name == skill_name and (actual / "SKILL.md").is_file():
        return CheckResult(
            name=name,
            status=Status.OK,
            reason=f"-> {actual} (NOTE: different CCST install than this one at {expected})",
        )
    return CheckResult(
        name=name,
        status=Status.FAIL,
        reason=(
            f"symlink points to {actual}, which is not a valid {skill_name!r} "
            f"skill directory (expected {expected} or another CCST install)"
        ),
    )


def check_pypi_version(installed_version: str, timeout: float = 3.0) -> CheckResult:
    """Compare installed version against the latest on PyPI.

    Skips silently (OK, no network) if the request fails.
    """
    name = "version:pypi"
    try:
        import httpx  # optional dep — present in the installed wheel

        r = httpx.get(
            "https://pypi.org/pypi/cc-session-tools/json",
            timeout=timeout,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return CheckResult(name=name, status=Status.OK, reason="PyPI query failed (skipped)")
        latest = r.json()["info"]["version"]
        if _version_tuple(latest) > _version_tuple(installed_version):
            return CheckResult(
                name=name,
                status=Status.WARN,
                reason=f"installed {installed_version}, latest {latest} available on PyPI",
            )
        return CheckResult(
            name=name,
            status=Status.OK,
            reason=f"installed {installed_version} is up to date",
        )
    except Exception:
        # Network failure, import error, etc. — don't fail doctor for this
        return CheckResult(
            name=name,
            status=Status.OK,
            reason="PyPI check skipped (network unavailable or httpx not installed)",
        )


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse a simple a.b.c version string into a comparable tuple."""
    parts: list[int] = []
    for segment in v.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


# ---------- high-level runner ----------


def run_all_checks(
    *,
    installed_version: str,
    settings_path: Path,
    bundle_path: Path,
    skills_source_dir: Path | None,
    skills_target_dir: Path,
    env: dict[str, str | None],
    skip_pypi: bool = False,
) -> list[CheckResult]:
    """Run the full doctor suite and return results.

    Parameters
    ----------
    installed_version:
        The ``__version__`` string of the installed package.
    settings_path:
        Path to ``~/.claude/settings.json``.
    bundle_path:
        Path to the bundled ``config/hooks-bundle.json``.
    skills_source_dir:
        Bundled skills directory (may be None if discovery fails).
    skills_target_dir:
        Target skills directory (usually ``~/.claude/skills/``).
    env:
        Dict with relevant env var names → values (or None).
    skip_pypi:
        If True, skip the PyPI version check.
    """
    results: list[CheckResult] = []

    # CLI presence
    for cli in ("ccd", "ccr", "ccs", "claude-code-usage", "ccst", "ccmsg"):
        results.append(check_cli_on_path(cli))

    # Environment variables
    for var in ("CLAUDE_SESSION_TOOLS_REPO_ROOT", "CLAUDE_SESSION_TOOLS_PROJ_ROOT"):
        results.append(check_env_dir(var, env.get(var)))

    # settings.json validity
    results.append(check_settings_json(settings_path))

    # Hook registrations (from bundle)
    settings_data: dict[str, Any] = {}
    if settings_path.exists():
        try:
            with settings_path.open() as f:
                settings_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    bundle_hooks = _extract_bundle_hook_names(bundle_path)
    for hook_name in bundle_hooks:
        results.append(check_hook_registered(hook_name, settings_data))

    # Skill symlinks
    if skills_source_dir is not None and skills_source_dir.is_dir():
        for skill_dir in sorted(skills_source_dir.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                results.append(
                    check_skill_symlink(skill_dir.name, skill_dir, skills_target_dir)
                )
    else:
        results.append(
            CheckResult(
                name="skills:source-dir",
                status=Status.WARN,
                reason="bundled skills/ directory not found; skill checks skipped",
            )
        )

    # PyPI version check
    if not skip_pypi:
        results.append(check_pypi_version(installed_version))

    return results


def _extract_bundle_hook_names(bundle_path: Path) -> list[str]:
    """Return hook names derived from the bundle file.

    Each hook entry has a command like ``ccst hooks run <name>``; we extract
    ``<name>``.
    """
    names: list[str] = []
    if not bundle_path.exists():
        return names
    try:
        with bundle_path.open() as f:
            bundle = json.load(f)
        for _event, blocks in bundle.get("hooks", {}).items():
            for block in blocks:
                for hook_entry in block.get("hooks", []):
                    cmd = hook_entry.get("command", "")
                    prefix = "ccst hooks run "
                    if cmd.startswith(prefix):
                        name = cmd[len(prefix):].strip()
                        if name and name not in names:
                            names.append(name)
    except (json.JSONDecodeError, OSError):
        pass
    return names


def format_results(results: list[CheckResult]) -> str:
    """Return a human-readable table of check results."""
    if not results:
        return "(no checks ran)"
    name_w = max(len(r.name) for r in results)
    lines = []
    for r in results:
        lines.append(f"[{r.status.value:<4}] {r.name:<{name_w}}  {r.reason}")
    return "\n".join(lines)


# ---------- drift monitor (ccst doctor --drift) ----------


def filter_unmuted_issues(
    results: list[CheckResult], muted: set[str]
) -> list[CheckResult]:
    """Return the WARN/FAIL results whose ``name`` is not muted.

    OK results never appear; muted names are dropped. Order is preserved.
    """
    return [
        r
        for r in results
        if r.status in (Status.WARN, Status.FAIL) and r.name not in muted
    ]


def format_drift_report(unmuted: list[CheckResult], *, muted_count: int) -> str:
    """Format the drift report for the monitor job.

    Returns the empty string when there is nothing un-muted to report — the
    caller prints nothing and exits 0 in that case, so a clean run produces no
    surfaced output.
    """
    if not unmuted:
        return ""
    name_w = max(len(r.name) for r in unmuted)
    lines = ["ccst doctor: un-muted drift detected —"]
    for r in unmuted:
        lines.append(f"  [{r.status.value:<4}] {r.name:<{name_w}}  {r.reason}")
    lines.append("")
    lines.append(
        "Acknowledge an item with:  ccst doctor --mute <name>"
        f"   ({muted_count} already muted)"
    )
    return "\n".join(lines)
