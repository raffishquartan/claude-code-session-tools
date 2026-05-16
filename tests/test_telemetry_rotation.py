"""Tests for telemetry.maybe_rotate (the new number-based rotation scheme)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cccs_hooks.telemetry import _ROTATION_BYTES, _ROTATION_KEEP, maybe_rotate


# ---------- maybe_rotate ----------

def test_maybe_rotate_below_threshold_returns_false(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("small")
    assert maybe_rotate(fires) is False
    assert fires.exists()


def test_maybe_rotate_nonexistent_file_returns_false(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    assert maybe_rotate(fires) is False


def test_maybe_rotate_above_threshold_returns_true(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("x" * (_ROTATION_BYTES + 1))
    assert maybe_rotate(fires) is True


def test_maybe_rotate_creates_slot_1(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    content = "x" * (_ROTATION_BYTES + 1)
    fires.write_text(content)
    maybe_rotate(fires)
    slot1 = tmp_path / "fires.jsonl.1"
    assert slot1.exists()
    assert slot1.read_text() == content


def test_maybe_rotate_removes_current_file(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("x" * (_ROTATION_BYTES + 1))
    maybe_rotate(fires)
    assert not fires.exists()


def test_maybe_rotate_shifts_existing_slots(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    slot1 = tmp_path / "fires.jsonl.1"
    slot1.write_text("old-slot-1")
    fires.write_text("x" * (_ROTATION_BYTES + 1))
    maybe_rotate(fires)
    assert (tmp_path / "fires.jsonl.2").read_text() == "old-slot-1"


def test_maybe_rotate_drops_slot_beyond_keep(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    for i in range(1, _ROTATION_KEEP + 1):
        (tmp_path / f"fires.jsonl.{i}").write_text(f"slot{i}")
    fires.write_text("x" * (_ROTATION_BYTES + 1))
    maybe_rotate(fires)
    # Oldest slot should be gone
    assert not (tmp_path / f"fires.jsonl.{_ROTATION_KEEP + 1}").exists()
    # Keep-slot itself (slot3) should be old slot2 content
    assert (tmp_path / f"fires.jsonl.{_ROTATION_KEEP}").read_text() == f"slot{_ROTATION_KEEP - 1}"


def test_maybe_rotate_custom_keep(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    for i in range(1, 3):
        (tmp_path / f"fires.jsonl.{i}").write_text(f"s{i}")
    fires.write_text("x" * (_ROTATION_BYTES + 1))
    maybe_rotate(fires, keep=2)
    # slot3 must NOT exist
    assert not (tmp_path / "fires.jsonl.3").exists()
    assert (tmp_path / "fires.jsonl.1").exists()
    assert (tmp_path / "fires.jsonl.2").exists()


def test_maybe_rotate_custom_threshold(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("x" * 1024)  # 1 KB
    # 0.5 KB threshold → should rotate
    assert maybe_rotate(fires, max_size_mb=0.0005) is True


def test_maybe_rotate_at_threshold_not_rotated(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("x" * _ROTATION_BYTES)  # exactly at threshold
    # <= threshold → no rotation
    assert maybe_rotate(fires) is False


def test_rotation_constant_is_10mb() -> None:
    """Verify the module-level constant is 10 MB as per spec."""
    assert _ROTATION_BYTES == 10 * 1024 * 1024


def test_rotation_keep_constant() -> None:
    """Verify the keep constant is 3."""
    assert _ROTATION_KEEP == 3
