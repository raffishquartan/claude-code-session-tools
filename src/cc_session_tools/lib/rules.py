from __future__ import annotations

import re
from pathlib import Path

from .roots import (
    PROJ_ROOT_ENV,
    REPO_ROOT_ENV,
    is_strict_root,
    is_valid_session_cwd,
    load_session_roots,
    matched_session_root,
    proj_root,
    repo_root,
    strict_root_path,
)

PROJECT_NAME_STRICT_RE = re.compile(r"^[a-z0-9]+$")
TAG_SUFFIX_FORMAT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]*$")
DATE_PREFIX_RE = re.compile(r"^(\d{8})-(.*)$")
TAG_NEW_RE = re.compile(r"^(\d{8})-([a-zA-Z0-9][a-zA-Z0-9-]*)$")

__all__ = [
    "DATE_PREFIX_RE",
    "PROJECT_NAME_STRICT_RE",
    "PROJ_ROOT_ENV",
    "REPO_ROOT_ENV",
    "TAG_NEW_RE",
    "TAG_SUFFIX_FORMAT_RE",
    "check_session_destination",
    "check_session_init",
    "encode_cwd",
    "is_strict_root",
    "is_valid_session_cwd",
    "load_session_roots",
    "matched_session_root",
    "proj_root",
    "repo_root",
    "strict_root_path",
    "validate_new_tag",
    "validate_strict_project_name",
    "validate_strict_tag_suffix",
    "validate_tag_suffix_no_spaces",
]


def _no_roots_error() -> str:
    return (
        f"no session roots configured: set ${REPO_ROOT_ENV} (loose, no naming "
        f"conventions) and/or ${PROJ_ROOT_ENV} (strict, requires "
        f"<project>-<label> tag) to a directory whose direct children are your "
        f"projects"
    )


def _cwd_not_under_root_error(cwd_abs: Path, roots: list[Path], *, dst: bool = False) -> str:
    label = "destination cwd" if dst else "cwd"
    return (
        f"{label} not a direct subdirectory of any configured root:\n"
        f"  {label}:  {cwd_abs}\n"
        f"  roots: {[str(r) for r in roots]}\n"
        f"  configure via ${REPO_ROOT_ENV} and/or ${PROJ_ROOT_ENV}"
    )


def validate_strict_project_name(project_name: str) -> str:
    if not PROJECT_NAME_STRICT_RE.match(project_name):
        return (
            f"project name '{project_name}' must match [a-z0-9]+ (no dashes, "
            f"no uppercase) under the strict (PROJ) root"
        )
    return ""


def validate_strict_tag_suffix(tag_suffix: str, project_name: str) -> str:
    prefix = project_name + "-"
    if not tag_suffix.startswith(prefix):
        return (
            f"tag suffix '{tag_suffix}' must start with '{prefix}' followed by a "
            f"descriptive label (e.g. '{project_name}-config-cleanup')"
        )
    rest = tag_suffix[len(prefix):]
    if not rest or not any(c.isalnum() for c in rest):
        return (
            f"tag suffix '{tag_suffix}' must have a descriptive label after "
            f"'{prefix}' containing at least one alphanumeric character "
            f"(e.g. '{project_name}-config-cleanup')"
        )
    return ""


def validate_tag_suffix_no_spaces(tag_suffix: str) -> str:
    if " " in tag_suffix:
        return f"tag must not contain spaces (use dashes): {tag_suffix!r}"
    return ""


def validate_new_tag(new_tag: str, original_tag: str) -> str:
    if " " in new_tag:
        return f"new tag must not contain spaces: {new_tag!r}"
    if "_" in new_tag:
        return f"new tag must not contain underscores (use dashes): {new_tag!r}"
    if "--" in new_tag:
        return f"new tag must not contain double-dashes: {new_tag!r}"
    if new_tag.endswith("-"):
        return f"new tag must not end with a dash: {new_tag!r}"
    m = TAG_NEW_RE.match(new_tag)
    if not m:
        return (
            f"new tag must match ^YYYYMMDD-<alphanumeric-with-dashes>$ "
            f"(letters, digits, dashes; first char alphanumeric): {new_tag!r}"
        )
    orig_m = re.match(r"^(\d{8})-", original_tag)
    if not orig_m:
        return f"original tag has no YYYYMMDD- prefix to preserve: {original_tag!r}"
    if m.group(1) != orig_m.group(1):
        return (
            f"date prefix is immutable: original is {orig_m.group(1)!r}, "
            f"new is {m.group(1)!r}"
        )
    return ""


def check_session_init(
    cwd_abs: Path, tag_suffix: str, force: bool = False
) -> tuple[bool, list[str]]:
    errors: list[str] = []

    err = validate_tag_suffix_no_spaces(tag_suffix)
    if err:
        errors.append(err)

    if not force:
        roots = load_session_roots()
        if not roots:
            errors.append(_no_roots_error())
        else:
            root = matched_session_root(cwd_abs, roots)
            if root is None:
                errors.append(_cwd_not_under_root_error(cwd_abs, roots))
            elif is_strict_root(root):
                project_name = cwd_abs.name
                err = validate_strict_project_name(project_name)
                if err:
                    errors.append(err)
                err = validate_strict_tag_suffix(tag_suffix, project_name)
                if err:
                    errors.append(err)

    return (not errors, errors)


def check_session_destination(
    dst_cwd_abs: Path,
    dst_tag: str,
    src_tag: str | None,
    force: bool = False,
) -> tuple[bool, list[str]]:
    errors: list[str] = []

    if src_tag is not None and dst_tag != src_tag:
        err = validate_new_tag(dst_tag, src_tag)
        if err:
            errors.append(err)

    m = DATE_PREFIX_RE.match(dst_tag)
    tag_suffix = m.group(2) if m else dst_tag

    if not force:
        roots = load_session_roots()
        if not roots:
            errors.append(_no_roots_error())
        else:
            root = matched_session_root(dst_cwd_abs, roots)
            if root is None:
                errors.append(_cwd_not_under_root_error(dst_cwd_abs, roots, dst=True))
            elif is_strict_root(root):
                project_name = dst_cwd_abs.name
                err = validate_strict_project_name(project_name)
                if err:
                    errors.append(err)
                err = validate_strict_tag_suffix(tag_suffix, project_name)
                if err:
                    errors.append(err)

    return (not errors, errors)


def encode_cwd(abs_path: str) -> str:
    if not abs_path.startswith("/"):
        raise ValueError(f"Expected absolute path, got {abs_path!r}")
    return abs_path.replace("/", "-")
