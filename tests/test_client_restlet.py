from __future__ import annotations

import pytest

from sanmar_netsuite.config import NetSuiteConfig, NetSuiteFieldMap
from sanmar_netsuite.netsuite.client import NetSuiteClient


def _sandbox_config(**over) -> NetSuiteConfig:
    base = dict(
        account_id="1234567_SB1",
        consumer_key="ck",
        consumer_secret="cs",
        token_id="ti",
        token_secret="ts",
        rest_base="https://1234567-sb1.suitetalk.api.netsuite.com",
        restlet_base="https://1234567-sb1.restlets.api.netsuite.com",
        matrix_script_id="123",
        matrix_deploy_id="1",
        sanmar_vendor_id="",
        subsidiary_id="",
        income_account_id="",
        asset_account_id="",
        cogs_account_id="",
        price_level_base="1",
        price_level_case="",
        price_level_msrp="",
        fields=NetSuiteFieldMap.from_env(),
    )
    base.update(over)
    return NetSuiteConfig(**base)


class _FakeResp:
    status_code = 200
    content = b"{}"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_call_restlet_builds_signed_url_and_returns_json(monkeypatch):
    client = NetSuiteClient(_sandbox_config())
    captured = {}

    def fake_request(method, url, *, json_body=None, headers=None):
        captured.update(method=method, url=url, body=json_body)
        return _FakeResp({"results": [{"externalId": "SANMAR-1", "status": "created", "id": 900}]})

    monkeypatch.setattr(client, "_request", fake_request)

    out = client.call_restlet("123", "1", {"items": [{"style": "2000"}]})

    assert captured["method"] == "POST"
    assert captured["url"] == (
        "https://1234567-sb1.restlets.api.netsuite.com"
        "/app/site/hosting/restlet.nl?script=123&deploy=1"
    )
    assert captured["body"] == {"items": [{"style": "2000"}]}
    assert out["results"][0]["status"] == "created"


def test_call_restlet_requires_script_and_deploy_ids():
    client = NetSuiteClient(_sandbox_config())
    with pytest.raises(RuntimeError, match="script/deploy ids"):
        client.call_restlet("", "", {"items": []})
