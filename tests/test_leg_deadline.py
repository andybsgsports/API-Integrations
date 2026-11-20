"""Tests for the leg deadline."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_status  # noqa: E402


def test_leg_deadline(monkeypatch):
    monkeypatch.delenv("LEG_DEADLINE_EPOCH", raising=False)
    assert run_status.leg_deadline() is None
    assert run_status.past_deadline(None) is False
    monkeypatch.setenv("LEG_DEADLINE_EPOCH", "100")
    assert run_status.leg_deadline() == 100.0
    assert run_status.past_deadline(100.0) is True
    monkeypatch.setenv("LEG_DEADLINE_EPOCH", "not a number")
    assert run_status.leg_deadline() is None

