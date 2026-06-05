"""The sandbox-first guardrail must block production client construction."""

from __future__ import annotations

import pytest

from sanmar_netsuite.config import NetSuiteConfig, NetSuiteFieldMap
from sanmar_netsuite.netsuite.client import NetSuiteClient


def _config(account_id: str, *, allow_prod: bool = False) -> NetSuiteConfig:
    base = f"https://{account_id.lower().replace('_', '-')}.suitetalk.api.netsuite.com"
    return NetSuiteConfig(
        account_id=account_id,
        consumer_key="ck",
        consumer_secret="cs",
        token_id="ti",
        token_secret="ts",
        rest_base=base,
        sanmar_vendor_id="42",
        subsidiary_id="1",
        income_account_id="101",
        asset_account_id="102",
        cogs_account_id="103",
        price_level_base="1",
        price_level_case="",
        price_level_msrp="",
        allow_production_writes=allow_prod,
        fields=NetSuiteFieldMap.from_env(),
    )


def test_sandbox_account_is_allowed():
    cfg = _config("1234567_SB1")
    assert cfg.is_sandbox is True
    # Should construct without raising.
    NetSuiteClient(cfg)


def test_release_preview_account_is_allowed():
    assert _config("1234567_RP").is_sandbox is True


def test_production_account_is_blocked_by_default():
    cfg = _config("1234567")
    assert cfg.is_sandbox is False
    with pytest.raises(RuntimeError, match="non-sandbox"):
        NetSuiteClient(cfg)


def test_production_account_allowed_with_explicit_flag():
    cfg = _config("1234567", allow_prod=True)
    # Explicit opt-in lets it through.
    NetSuiteClient(cfg)
