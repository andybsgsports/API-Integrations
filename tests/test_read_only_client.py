"""NETSUITE_READ_ONLY: the client must refuse anything that could write.

This is the hard stop behind the production dry run (Andy, 2026-11-13): a
script's own dry-run handling is not the only thing between it and
production.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sanmar_netsuite.netsuite.client import (  # noqa: E402
    NetSuiteClient,
    NetSuiteReadOnlyError,
)
from test_production_guard import _config  # noqa: E402


class _Resp:
    status_code = 200
    reason = "OK"
    headers: dict = {}
    text = "{}"
    content = b"{}"

    def json(self):
        return {"items": [{"n": 1}], "hasMore": False}


class _Session:
    def __init__(self):
        self.sent = []

    def request(self, method, url, **_kw):
        self.sent.append((method, url))
        return _Resp()


def _client(read_only=True):
    cfg = dataclasses.replace(
        _config("1234567", allow_prod=True),
        read_only=read_only,
        restlet_base="https://1234567.restlets.api.netsuite.com",
        matrix_script_id="customscript_x",
        matrix_deploy_id="customdeploy_x",
    )
    client = NetSuiteClient(cfg)
    client._session = _Session()
    return client


def test_suiteql_and_get_are_allowed():
    c = _client()
    assert c.suiteql("SELECT 1") == [{"n": 1}]
    c._request("GET", c._url("/services/rest/record/v1/inventoryItem/1"))
    assert len(c._session.sent) == 2


@pytest.mark.parametrize("call", [
    lambda c: c.create_record("inventoryItem", {"itemId": "X"}),
    lambda c: c.update_record("inventoryItem", "1", {"displayName": "X"}),
    lambda c: c.call_restlet("customscript_x", "customdeploy_x", {"items": []}),
    lambda c: c.call_restlet("customscript_x", "customdeploy_x", None, method="GET"),
    lambda c: c._request("DELETE", c._url("/services/rest/record/v1/inventoryItem/1")),
])
def test_writes_and_restlets_are_refused_before_sending(call):
    c = _client()
    with pytest.raises(NetSuiteReadOnlyError):
        call(c)
    assert c._session.sent == []


def test_off_by_default():
    c = _client(read_only=False)
    c.update_record("inventoryItem", "1", {"displayName": "X"})
    assert c._session.sent
