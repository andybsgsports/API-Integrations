"""The SOAP helper refuses writes while read-only is set."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ns_soap  # noqa: E402


def test_update_is_refused_and_nothing_is_posted(monkeypatch):
    monkeypatch.setattr(ns_soap.requests, "post", lambda *a, **k: pytest.fail("posted"))
    cfg = SimpleNamespace(read_only=True)
    for action in ("update", "add", "upsert", "delete"):
        with pytest.raises(RuntimeError, match="NETSUITE_READ_ONLY"):
            ns_soap.post(cfg, action, "<x/>")
