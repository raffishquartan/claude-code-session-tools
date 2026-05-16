"""Tests for skills/generate-8digit-code/scripts/generate_8digit_code.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "generate_8digit_code.py"
)


def _run() -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    return result.stdout.strip()


# ── format checks ────────────────────────────────────────────────────────────

def test_output_is_exactly_8_digits() -> None:
    code = _run()
    assert len(code) == 8, f"Expected 8 chars, got {len(code)!r}: {code!r}"
    assert code.isdigit(), f"Expected digits only, got: {code!r}"


def test_leading_zero_preserved() -> None:
    """Run many times to ensure zero-padded codes are returned correctly.
    With 100_000_000 possible values, ~10% start with 0."""
    codes = [_run() for _ in range(20)]
    for c in codes:
        assert len(c) == 8, f"Padding lost: {c!r}"
        assert c.isdigit()


def test_exit_code_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0


def test_output_ends_with_newline() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.stdout.endswith("\n"), "Output must end with a newline"


def test_only_one_line_of_output() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    lines = result.stdout.splitlines()
    assert len(lines) == 1, f"Expected 1 line, got {lines!r}"


# ── randomness / uniqueness ───────────────────────────────────────────────────

def test_successive_calls_produce_different_codes() -> None:
    """10 successive calls must not all be identical.

    The probability of 10 identical draws from U[0, 10^8) is 10^-72 — if
    this test fails the PRNG is broken, not merely unlucky.
    """
    codes = {_run() for _ in range(10)}
    assert len(codes) > 1, "All 10 codes were identical — PRNG is broken"


def test_range_is_correct() -> None:
    """All generated codes must be in [00000000, 99999999]."""
    for _ in range(20):
        code = _run()
        value = int(code)
        assert 0 <= value <= 99_999_999, f"Out of range: {value}"


# ── importable module ─────────────────────────────────────────────────────────

def test_generate_function_importable() -> None:
    """The generate() function can be imported and returns a valid code."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen", SCRIPT)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]

    code = mod.generate()
    assert isinstance(code, str)
    assert len(code) == 8
    assert code.isdigit()
