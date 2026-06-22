"""Environment-driven configuration.

All runtime configuration is read from environment variables (loaded from a
local ``.env`` during development via python-dotenv). See ``.env.example`` for
the full list and documentation of each value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

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


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SftpConfig:
    host: str
    port: int
    username: str
    password: str
    remote_dir: str
    host_key: str
    download_dir: str

    @classmethod
    def from_env(cls) -> SftpConfig:
        return cls(
            host=_get("SANMAR_SFTP_HOST", "ftp.sanmar.com"),
            port=_get_int("SANMAR_SFTP_PORT", 2200),
            username=_get("SANMAR_SFTP_USERNAME"),
            password=_get("SANMAR_SFTP_PASSWORD"),
            remote_dir=_get("SANMAR_SFTP_REMOTE_DIR", "/SanMarPDD"),
            host_key=_get("SANMAR_SFTP_HOST_KEY"),
            download_dir=_get("SANMAR_DOWNLOAD_DIR", "./downloads"),
        )


@dataclass(frozen=True)
class NetSuiteFieldMap:
    """Internal-id / scriptid mapping for the custom NetSuite item fields that
    hold SanMar-specific data. These are configured per account."""

    unique_key: str
    inventory_key: str
    size_index: str
    style: str
    mainframe_color: str
    gtin: str
    map_price: str
    msrp: str
    case_price: str
    case_size: str
    product_status: str
    qty_available: str
    qty_by_whse: str
    front_image_url: str
    image_file: str

    @classmethod
    def from_env(cls) -> NetSuiteFieldMap:
        return cls(
            unique_key=_get("NS_FIELD_SANMAR_UNIQUE_KEY", "custitem_sanmar_unique_key"),
            inventory_key=_get("NS_FIELD_SANMAR_INVENTORY_KEY", "custitem_sanmar_inventory_key"),
            size_index=_get("NS_FIELD_SANMAR_SIZE_INDEX", "custitem_sanmar_size_index"),
            style=_get("NS_FIELD_SANMAR_STYLE", "custitem_sanmar_style"),
            mainframe_color=_get("NS_FIELD_SANMAR_MAINFRAME_COLOR", "custitem_sanmar_mf_color"),
            gtin=_get("NS_FIELD_SANMAR_GTIN", "custitem_sanmar_gtin"),
            map_price=_get("NS_FIELD_SANMAR_MAP", "custitem_sanmar_map"),
            msrp=_get("NS_FIELD_SANMAR_MSRP", "custitem_sanmar_msrp"),
            case_price=_get("NS_FIELD_SANMAR_CASE_PRICE", "custitem_sanmar_case_price"),
            case_size=_get("NS_FIELD_SANMAR_CASE_SIZE", "custitem_sanmar_case_size"),
            product_status=_get("NS_FIELD_SANMAR_PRODUCT_STATUS", "custitem_sanmar_status"),
            qty_available=_get("NS_FIELD_SANMAR_QTY_AVAILABLE", "custitem_sanmar_qty_available"),
            qty_by_whse=_get("NS_FIELD_SANMAR_QTY_BY_WHSE", "custitem_sanmar_qty_by_whse"),
            front_image_url=_get(
                "NS_FIELD_SANMAR_FRONT_IMAGE_URL", "custitem_sanmar_front_image_url"
            ),
            image_file=_get("NS_FIELD_SANMAR_IMAGE_FILE", "custitem_sanmar_image"),
        )


@dataclass(frozen=True)
class NetSuiteConfig:
    account_id: str
    consumer_key: str
    consumer_secret: str
    token_id: str
    token_secret: str
    rest_base: str
    sanmar_vendor_id: str
    subsidiary_id: str
    income_account_id: str
    asset_account_id: str
    cogs_account_id: str
    price_level_base: str
    price_level_case: str
    price_level_msrp: str
    allow_production_writes: bool = False
    fields: NetSuiteFieldMap = field(default_factory=NetSuiteFieldMap.from_env)

    @property
    def is_sandbox(self) -> bool:
        """True when the account id targets a NetSuite sandbox/release-preview.

        Sandbox realms look like ``1234567_SB1`` (REST host ``1234567-sb1``);
        release-preview is ``_RP``. Anything else is treated as production.
        """
        ident = f"{self.account_id} {self.rest_base}".lower()
        return any(token in ident for token in ("_sb", "-sb", "_rp", "-rp"))

    @classmethod
    def from_env(cls) -> NetSuiteConfig:
        account_id = _get("NETSUITE_ACCOUNT_ID")
        # REST host: account id lowercased with underscores → hyphens.
        derived_base = (
            f"https://{account_id.lower().replace('_', '-')}.suitetalk.api.netsuite.com"
            if account_id
            else ""
        )
        return cls(
            account_id=account_id,
            consumer_key=_get("NETSUITE_CONSUMER_KEY"),
            consumer_secret=_get("NETSUITE_CONSUMER_SECRET"),
            token_id=_get("NETSUITE_TOKEN_ID"),
            token_secret=_get("NETSUITE_TOKEN_SECRET"),
            rest_base=_get("NETSUITE_REST_BASE", derived_base),
            sanmar_vendor_id=_get("NETSUITE_SANMAR_VENDOR_ID"),
            subsidiary_id=_get("NETSUITE_SUBSIDIARY_ID"),
            income_account_id=_get("NETSUITE_INCOME_ACCOUNT_ID"),
            asset_account_id=_get("NETSUITE_ASSET_ACCOUNT_ID"),
            cogs_account_id=_get("NETSUITE_COGS_ACCOUNT_ID"),
            price_level_base=_get("NETSUITE_PRICE_LEVEL_BASE", "1"),
            price_level_case=_get("NETSUITE_PRICE_LEVEL_CASE"),
            price_level_msrp=_get("NETSUITE_PRICE_LEVEL_MSRP"),
            allow_production_writes=_get_bool("NETSUITE_ALLOW_PRODUCTION_WRITES", False),
            fields=NetSuiteFieldMap.from_env(),
        )


@dataclass(frozen=True)
class SyncConfig:
    state_db_path: str
    max_records: int
    dry_run: bool
    log_level: str
    tax_schedule: str
    income_account: str

    @classmethod
    def from_env(cls) -> SyncConfig:
        return cls(
            state_db_path=_get("STATE_DB_PATH", "./state/sync_state.db"),
            max_records=_get_int("SYNC_MAX_RECORDS", 0),
            dry_run=_get_bool("SYNC_DRY_RUN", True),
            log_level=_get("LOG_LEVEL", "INFO"),
            tax_schedule=_get("SYNC_TAX_SCHEDULE", "Taxable"),
            income_account=_get("SYNC_INCOME_ACCOUNT", "4100 SALES OF MERCHANDISE"),
        )


@dataclass(frozen=True)
class AppConfig:
    sftp: SftpConfig
    netsuite: NetSuiteConfig
    sync: SyncConfig

    @classmethod
    def from_env(cls) -> AppConfig:
        return cls(
            sftp=SftpConfig.from_env(),
            netsuite=NetSuiteConfig.from_env(),
            sync=SyncConfig.from_env(),
        )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return the process-wide configuration (cached)."""
    return AppConfig.from_env()
