"""Environment-driven configuration for S&S Activewear → NetSuite.

Shares the NetSuite-side config with the SanMar integration (same auth, same
realm) but defines its own API-side credentials and its own custom-field map
so SanMar and S&S item data don't collide.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

from sanmar_netsuite.config import NetSuiteConfig, SyncConfig

load_dotenv()


def _get(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value or ""


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class SsApiConfig:
    """HTTP Basic auth + base URL for ``api.ssactivewear.com``.

    The S&S API uses HTTP Basic with the API account number as the username
    and the API key as the password. Account number is shown in the S&S
    Account Management portal; the API key is generated under API Access.
    """

    base_url: str
    account_number: str
    api_key: str
    request_timeout: int
    page_size: int

    @classmethod
    def from_env(cls) -> SsApiConfig:
        return cls(
            base_url=_get("SS_API_BASE_URL", "https://api.ssactivewear.com/v2"),
            account_number=_get("SS_API_ACCOUNT_NUMBER"),
            api_key=_get("SS_API_KEY"),
            request_timeout=_get_int("SS_API_TIMEOUT", 60),
            page_size=_get_int("SS_API_PAGE_SIZE", 500),
        )


@dataclass(frozen=True)
class SsNetSuiteFieldMap:
    """Scriptids for the custom NetSuite item fields that hold S&S-specific data.

    Kept distinct from the SanMar map so the two suppliers' data live in
    parallel columns on each item (useful when a NetSuite item maps to both
    a SanMar SKU and an S&S SKU — e.g. cross-sourced styles).
    """

    sku: str
    style_id: str
    style_name: str
    color_name: str
    color_code: str
    size_name: str
    size_order: str
    gtin: str
    brand: str
    map_price: str
    msrp: str
    piece_price: str
    dozen_price: str
    case_price: str
    case_size: str
    weight: str
    qty_available: str
    qty_by_whse: str
    is_closeout: str
    is_discontinued: str
    front_image_url: str
    on_model_image_url: str

    @classmethod
    def from_env(cls) -> SsNetSuiteFieldMap:
        return cls(
            sku=_get("NS_FIELD_SS_SKU", "custitem_ss_sku"),
            style_id=_get("NS_FIELD_SS_STYLE_ID", "custitem_ss_style_id"),
            style_name=_get("NS_FIELD_SS_STYLE", "custitem_ss_style"),
            color_name=_get("NS_FIELD_SS_COLOR_NAME", "custitem_ss_color_name"),
            color_code=_get("NS_FIELD_SS_COLOR_CODE", "custitem_ss_color_code"),
            size_name=_get("NS_FIELD_SS_SIZE_NAME", "custitem_ss_size_name"),
            size_order=_get("NS_FIELD_SS_SIZE_ORDER", "custitem_ss_size_order"),
            gtin=_get("NS_FIELD_SS_GTIN", "custitem_ss_gtin"),
            brand=_get("NS_FIELD_SS_BRAND", "custitem_ss_brand"),
            map_price=_get("NS_FIELD_SS_MAP", "custitem_ss_map"),
            msrp=_get("NS_FIELD_SS_MSRP", "custitem_ss_msrp"),
            piece_price=_get("NS_FIELD_SS_PIECE_PRICE", "custitem_ss_piece_price"),
            dozen_price=_get("NS_FIELD_SS_DOZEN_PRICE", "custitem_ss_dozen_price"),
            case_price=_get("NS_FIELD_SS_CASE_PRICE", "custitem_ss_case_price"),
            case_size=_get("NS_FIELD_SS_CASE_SIZE", "custitem_ss_case_size"),
            weight=_get("NS_FIELD_SS_WEIGHT", "custitem_ss_weight"),
            qty_available=_get("NS_FIELD_SS_QTY_AVAILABLE", "custitem_ss_qty_available"),
            qty_by_whse=_get("NS_FIELD_SS_QTY_BY_WHSE", "custitem_ss_qty_by_whse"),
            is_closeout=_get("NS_FIELD_SS_IS_CLOSEOUT", "custitem_ss_is_closeout"),
            is_discontinued=_get("NS_FIELD_SS_IS_DISCONTINUED", "custitem_ss_is_discontinued"),
            front_image_url=_get("NS_FIELD_SS_FRONT_IMAGE_URL", "custitem_ss_front_image_url"),
            on_model_image_url=_get(
                "NS_FIELD_SS_ON_MODEL_IMAGE_URL", "custitem_ss_on_model_image_url"
            ),
        )


@dataclass(frozen=True)
class SsAppConfig:
    """Top-level config bundle for the S&S → NetSuite integration."""

    ss_api: SsApiConfig
    netsuite: NetSuiteConfig
    netsuite_fields: SsNetSuiteFieldMap
    sync: SyncConfig
    ss_vendor_id: str = field(default="")
    image_folder_id: str = field(default="")
    download_dir: str = field(default="./downloads/ss")

    @classmethod
    def from_env(cls) -> SsAppConfig:
        return cls(
            ss_api=SsApiConfig.from_env(),
            netsuite=NetSuiteConfig.from_env(),
            netsuite_fields=SsNetSuiteFieldMap.from_env(),
            sync=SyncConfig.from_env(),
            ss_vendor_id=_get("NETSUITE_SS_VENDOR_ID"),
            image_folder_id=_get("NETSUITE_SS_IMAGE_FOLDER_ID"),
            download_dir=_get("SS_DOWNLOAD_DIR", "./downloads/ss"),
        )


@lru_cache(maxsize=1)
def get_config() -> SsAppConfig:
    """Return the process-wide S&S config (cached)."""
    return SsAppConfig.from_env()
