"""HTTP client for the S&S Activewear REST API (api.ssactivewear.com/v2).

Authentication is HTTP Basic with the API account number (username) and
API key (password). All responses are JSON. The client transparently:

* retries 429s and 5xxs with exponential backoff,
* pages list endpoints that support ``offset``/``pageSize`` query params,
* converts JSON dicts into :class:`~ss_activewear_netsuite.models.SsProduct`
  / :class:`~ss_activewear_netsuite.models.SsStyle` instances.

The client is intentionally small — anything beyond list/detail GET is
delegated to the caller.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Any

import requests
from requests.auth import HTTPBasicAuth
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import SsApiConfig
from ..models import SsProduct, SsStyle, WarehouseQty

log = logging.getLogger(__name__)


class SsApiError(RuntimeError):
    """Raised on non-success HTTP responses from S&S."""

    def __init__(self, status: int, message: str, payload: Any = None) -> None:
        super().__init__(f"S&S {status}: {message}")
        self.status = status
        self.payload = payload


class _RetryableHTTP(RuntimeError):
    """Internal marker for 429/5xx responses worth retrying."""


def _decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (ValueError, ArithmeticError):
        return None


def _int(raw: Any, default: int = 0) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return default


def _bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"true", "1", "yes", "y"}
    return bool(raw)


def _warehouses(raw: Any) -> tuple[WarehouseQty, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[WarehouseQty] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        abbr = str(entry.get("warehouseAbbr") or entry.get("warehouse") or "").strip()
        qty = _int(entry.get("qty"))
        if abbr:
            out.append(WarehouseQty(warehouse_abbr=abbr, qty=qty))
    return tuple(out)


def product_from_payload(row: dict[str, Any]) -> SsProduct:
    """Convert one S&S ``/Products`` JSON row into an :class:`SsProduct`."""

    return SsProduct(
        sku=str(row.get("sku") or row.get("skuID_Master") or "").strip(),
        style_id=str(row.get("styleID") or "").strip(),
        style_name=str(row.get("styleName") or "").strip(),
        brand_name=str(row.get("brandName") or "").strip(),
        color_name=str(row.get("colorName") or "").strip(),
        color_code=str(row.get("colorCode") or "").strip(),
        color_price_code=str(row.get("colorPriceCodeName") or "").strip(),
        size_name=str(row.get("sizeName") or "").strip(),
        size_order=_int(row.get("sizeOrder")),
        gtin=str(row.get("gtin") or "").strip(),
        weight=_decimal(row.get("weight")),
        case_size=_int(row.get("caseSize")) or None,
        piece_price=_decimal(row.get("piecePrice")),
        dozen_price=_decimal(row.get("dozenPrice")),
        case_price=_decimal(row.get("casePrice")),
        sale_price=_decimal(row.get("salePrice")),
        customer_price=_decimal(row.get("customerPrice")),
        map_price=_decimal(row.get("mapPrice")),
        msrp=_decimal(row.get("msrp")),
        qty_available=_int(row.get("qty")),
        # ``/Products`` (filtered) keys this "warehouseAvailability"; the
        # per-SKU ``/Inventory/{sku}`` endpoint keys the same shape "warehouses".
        warehouses=_warehouses(row.get("warehouseAvailability") or row.get("warehouses")),
        is_closeout=_bool(row.get("isCloseout")),
        is_discontinued=_bool(row.get("isDiscontinued")),
        front_image_url=str(row.get("colorFrontImage") or row.get("frontImage") or "").strip(),
        on_model_image_url=str(
            row.get("colorOnModelFrontImage") or row.get("onModelFrontImage") or ""
        ).strip(),
        description=str(row.get("description") or "").strip(),
        category_name=str(row.get("categoryName") or "").strip(),
    )


def style_from_payload(row: dict[str, Any]) -> SsStyle:
    """Convert one S&S ``/Styles`` JSON row into an :class:`SsStyle`."""

    return SsStyle(
        style_id=str(row.get("styleID") or "").strip(),
        style_name=str(row.get("styleName") or row.get("partNumber") or "").strip(),
        brand_name=str(row.get("brandName") or "").strip(),
        title=str(row.get("title") or "").strip(),
        description=str(row.get("description") or "").strip(),
        category_name=str(row.get("categoryName") or "").strip(),
    )


class SsClient:
    """Minimal REST client for ``api.ssactivewear.com``."""

    def __init__(self, config: SsApiConfig) -> None:
        if not config.account_number or not config.api_key:
            raise RuntimeError(
                "SS_API_ACCOUNT_NUMBER and SS_API_KEY must be set "
                "(copy .env.example to .env and fill them in)."
            )
        self._config = config
        self._session = requests.Session()
        self._auth = HTTPBasicAuth(config.account_number, config.api_key)

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}/{path.lstrip('/')}"

    @retry(
        retry=retry_if_exception_type(
            (_RetryableHTTP, requests.ConnectionError, requests.Timeout)
        ),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=16),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self._url(path)
        resp = self._session.get(
            url,
            auth=self._auth,
            params=params,
            headers={"Accept": "application/json"},
            timeout=self._config.request_timeout,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning("Retryable S&S response %s for GET %s", resp.status_code, url)
            raise _RetryableHTTP(f"{resp.status_code} {resp.reason}")
        if resp.status_code >= 400:
            try:
                detail = resp.json()
                message = detail.get("message") or resp.text
            except ValueError:
                detail = resp.text
                message = resp.text
            raise SsApiError(resp.status_code, str(message), detail)
        return resp.json() if resp.content else None

    # ── list endpoints (filtered, to dodge the unfiltered-Products throttle) ──
    # S&S throttles the unfiltered ``GET /Products`` pull (503 with a
    # ``RateLimit`` error directing callers to "use the Get Product filter
    # options"). So the full catalog is assembled style-by-style: enumerate
    # ``/Styles``, then pull products for batches of styleIDs via the filtered
    # ``?styleid=`` (comma-delimited) query, which S&S serves without throttling.
    #: How many styleIDs to request per filtered ``/Products`` call.
    STYLE_BATCH_SIZE = 100

    def iter_products(self, *, style_id: str | None = None) -> Iterator[SsProduct]:
        """Stream products via S&S's *filtered* Get Products endpoint.

        With ``style_id`` set, fetch just that one style. Otherwise enumerate
        every style and pull products in batched ``?styleid=`` requests. The
        unfiltered ``/Products`` pull is throttled by S&S and is never used.
        """
        if style_id is not None:
            yield from self._products_for_styles([str(style_id)])
            return
        batch: list[str] = []
        for style in self.iter_styles():
            if not style.style_id:
                continue
            batch.append(style.style_id)
            if len(batch) >= self.STYLE_BATCH_SIZE:
                yield from self._products_for_styles(batch)
                batch = []
        if batch:
            yield from self._products_for_styles(batch)

    def _products_for_styles(self, style_ids: list[str]) -> Iterator[SsProduct]:
        """One filtered Get Products call for a batch of styleIDs."""
        data = self._get("/Products", params={"styleid": ",".join(style_ids)})
        yield from self._parse_rows(data, product_from_payload)

    def iter_styles(self) -> Iterator[SsStyle]:
        """Stream styles. ``/Styles`` returns the whole list in a single
        response (it ignores pageSize/pageNumber), so we fetch it once."""
        data = self._get("/Styles")
        yield from self._parse_rows(data, style_from_payload)

    @staticmethod
    def _parse_rows(data: Any, parser: Any) -> Iterator[Any]:
        """Yield parsed rows from a JSON array (or ``{"items": [...]}``) body."""
        if data is None:
            return
        rows = data if isinstance(data, list) else data.get("items") or []
        for row in rows:
            yield parser(row)

    def get_product(self, sku: str) -> SsProduct | None:
        data = self._get(f"/Products/{sku}")
        if not data:
            return None
        if isinstance(data, list):
            return product_from_payload(data[0]) if data else None
        return product_from_payload(data)

    def get_inventory(self, sku: str) -> SsProduct | None:
        data = self._get(f"/Inventory/{sku}")
        if not data:
            return None
        if isinstance(data, list):
            return product_from_payload(data[0]) if data else None
        return product_from_payload(data)

    #: How many SKUs to request per batched ``/Inventory`` call.
    INVENTORY_BATCH_SIZE = 40

    def iter_inventory(self, skus: Iterable[str]) -> Iterator[SsProduct]:
        """Stream inventory records (incl. the per-warehouse breakdown) for
        the given SKUs via batched ``/Inventory/{id,id,...}`` calls.

        ``/Inventory`` is the only S&S endpoint that returns ``warehouses``;
        the filtered ``/Products`` pull never includes a per-warehouse
        breakdown (confirmed empirically). Only sku/gtin/style/warehouses
        are populated on the yielded products.
        """
        batch: list[str] = []
        for sku in skus:
            if not sku:
                continue
            batch.append(sku)
            if len(batch) >= self.INVENTORY_BATCH_SIZE:
                yield from self._inventory_for(batch)
                batch = []
        if batch:
            yield from self._inventory_for(batch)

    def _inventory_for(self, skus: list[str]) -> Iterator[SsProduct]:
        data = self._get("/Inventory/" + ",".join(skus))
        yield from self._parse_rows(data, product_from_payload)
