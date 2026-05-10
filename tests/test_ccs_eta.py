# tests/test_ccs_eta.py
from __future__ import annotations

import pytest

from cc_session_tools.cli.ccs import _compute_eta, _batch_sizes


def test_compute_eta_at_halfway():
    # X=5s, Y=5 done, Z=10 total -> 5 + (5/5)*(10-5) = 10s
    assert _compute_eta(elapsed=5.0, completed=5, total=10) == pytest.approx(10.0)


def test_compute_eta_at_start():
    # X=1s, Y=1 done, Z=100 total -> 1 + (1/1)*(99) = 100s
    assert _compute_eta(elapsed=1.0, completed=1, total=100) == pytest.approx(100.0)


def test_compute_eta_completed_zero_returns_inf():
    assert _compute_eta(elapsed=5.0, completed=0, total=10) == float("inf")


def test_batch_sizes_small():
    assert _batch_sizes(5) == [5]
    assert _batch_sizes(10) == [10]


def test_batch_sizes_medium():
    assert _batch_sizes(50) == [10, 40]
    assert _batch_sizes(110) == [10, 100]


def test_batch_sizes_large():
    assert _batch_sizes(200) == [10, 100, 90]
    assert _batch_sizes(111) == [10, 100, 1]


def test_batch_sizes_zero():
    assert _batch_sizes(0) == []
