"""Iteration tests for the S&S API client.

These verify how the client walks S&S's list endpoints *without* hitting the
network: ``_get`` is monkeypatched. The behavior under test exists because the
unfiltered ``GET /Products`` pull is throttled by S&S (503 RateLimit), so the
full catalog must be assembled style-by-style via the filtered ``?styleid=``
query.
"""

from __future__ import annotations

from typing import Any

from ss_activewear_netsuite.config import SsApiConfig
from ss_activewear_netsuite.ss_activewear.client import SsClient


def _client() -> SsClient:
    return SsClient(
        SsApiConfig(
            base_url="https://api.example.com/v2",
            account_number="07548",
            api_key="key",
            request_timeout=5,
            page_size=500,
        )
    )


def test_iter_products_batches_styleids_and_never_pulls_unfiltered(
    monkeypatch: Any,
) -> None:
    client = _client()
    client.STYLE_BATCH_SIZE = 2  # force several batches over 5 styles

    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_get(path: str, params: dict[str, Any] | None = None) -> Any:
        calls.append((path, params))
        if path == "/Styles":
            return [{"styleID": i} for i in range(1, 6)]  # 5 styles
        # filtered /Products: one row per requested styleID
        ids = (params or {})["styleid"].split(",")
        return [{"sku": f"SKU{sid}", "styleID": sid} for sid in ids]

    monkeypatch.setattr(client, "_get", fake_get)

    products = list(client.iter_products())

    # styleIDs are batched per STYLE_BATCH_SIZE: [1,2], [3,4], [5]
    product_calls = [params["styleid"] for path, params in calls if path == "/Products"]
    assert product_calls == ["1,2", "3,4", "5"]
    # every product is returned, in order
    assert [p.sku for p in products] == ["SKU1", "SKU2", "SKU3", "SKU4", "SKU5"]
    # the throttled, unfiltered /Products (no styleid filter) is never called
    assert all(params and "styleid" in params for path, params in calls if path == "/Products")


def test_iter_products_single_style_uses_filter(monkeypatch: Any) -> None:
    client = _client()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_get(path: str, params: dict[str, Any] | None = None) -> Any:
        calls.append((path, params))
        return [{"sku": "B49695500", "styleID": "9182"}]

    monkeypatch.setattr(client, "_get", fake_get)

    products = list(client.iter_products(style_id="9182"))

    assert [p.sku for p in products] == ["B49695500"]
    # scoped lookups go through the ?styleid= filter, not the /Products/{id} path
    assert calls == [("/Products", {"styleid": "9182"})]


def test_iter_styles_single_fetch(monkeypatch: Any) -> None:
    client = _client()
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_get(path: str, params: dict[str, Any] | None = None) -> Any:
        calls.append((path, params))
        return [{"styleID": 1, "styleName": "A"}, {"styleID": 2, "styleName": "B"}]

    monkeypatch.setattr(client, "_get", fake_get)

    styles = list(client.iter_styles())

    # /Styles ignores pagination and returns everything at once: one GET only.
    assert calls == [("/Styles", None)]
    assert [s.style_id for s in styles] == ["1", "2"]
