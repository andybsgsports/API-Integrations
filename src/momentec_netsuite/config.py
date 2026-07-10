"""Environment-driven configuration for Momentec Brands → NetSuite.

Feeds are public static CSVs (no auth); ``logonId``/``password`` are only
needed for the dealer-pricing API (``api.momentecbrands.com``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

_DEF = {
    "products": "https://static.momentecbrands.com/productdata/product-data-std-all.csv",
    "sublimation": "https://static.momentecbrands.com/productdata/sublimation-product-data-std-all.csv",
    "images": "https://static.momentecbrands.com/productdata/product-images-all.csv",
    "inventory": "https://static.augustasportswear.com/productdata/inventorydata/ASG_inventory_data.csv",
}


@dataclass(frozen=True)
class MomentecConfig:
    products_url: str
    sublimation_url: str
    images_url: str
    inventory_url: str
    logon_id: str
    password: str
    api_base_url: str
    download_dir: str

    @classmethod
    def from_env(cls) -> MomentecConfig:
        g = os.environ.get
        return cls(
            products_url=g("MOMENTEC_PRODUCTS_URL", _DEF["products"]),
            sublimation_url=g("MOMENTEC_SUBLIMATION_URL", _DEF["sublimation"]),
            images_url=g("MOMENTEC_IMAGES_URL", _DEF["images"]),
            inventory_url=g("MOMENTEC_INVENTORY_URL", _DEF["inventory"]),
            logon_id=g("MOMENTEC_LOGON_ID", ""),
            password=g("MOMENTEC_PASSWORD", ""),
            api_base_url=g("MOMENTEC_API_BASE_URL", "https://api.momentecbrands.com"),
            download_dir=g("MOMENTEC_DOWNLOAD_DIR", "./downloads/momentec"),
        )


@lru_cache(maxsize=1)
def get_config() -> MomentecConfig:
    return MomentecConfig.from_env()
