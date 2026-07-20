"""Parsers for SanMar product data files.

Three file formats are handled:

* ``SanMar_SDL_N.csv`` / ``SanMar_SDL_DI.csv`` — comma-delimited, quote
  encapsulated, one row per style/color/size. Carries catalog, pricing, MSRP/
  MAP, GTIN, product status, and image URLs. ``SanMar_EPDD.csv`` shares this
  schema and adds a ``QTY`` (all-warehouse) column, so the same reader handles
  both. (EPDD duplicates unique keys across category/subcategory — deduped here.)
* ``sanmar_dip.txt`` — pipe-delimited, one row per warehouse per SKU. SanMar's
  live export leads with a column-header line, which is skipped. Carries
  per-warehouse availability and live sale pricing.

Parsing is driven by the documented field names/indices in
:mod:`sanmar_netsuite.sanmar.constants`. Readers are tolerant of header
whitespace and the stray spaces present in some SanMar column headers
(e.g. ``BACK_MODEL _IMAGE_URL``).
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterable, Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..models import (
    ColorImages,
    InventoryRecord,
    SkuRecord,
    StyleRecord,
    WarehouseQty,
)
from . import constants as C

log = logging.getLogger(__name__)


# ── small value coercion helpers ─────────────────────────────────────────────
def _clean(value: str | None) -> str:
    return (value or "").strip()


def _decimal(value: str | None) -> Decimal | None:
    raw = _clean(value).replace("$", "").replace(",", "")
    if not raw or raw.upper() in {"NA", "N/A", "NULL"}:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _int(value: str | None) -> int | None:
    raw = _clean(value).replace(",", "")
    if not raw:
        return None
    try:
        return int(Decimal(raw))
    except (InvalidOperation, ValueError):
        return None


def _normalize_header(name: str) -> str:
    """Canonicalize a CSV header: upper-case, drop spaces/underscores/symbols.

    Keeps only alphanumerics and ``#`` (for ``STYLE#``) so the various SanMar
    header spellings — including the stray space in ``BACK_MODEL _IMAGE_URL`` —
    collapse onto the canonical alias keys in ``_SDL_ALIASES``.
    """
    return "".join(ch for ch in name.upper() if ch.isalnum() or ch == "#")


# Canonical header -> the model attribute it feeds. Multiple raw spellings map
# to the same canonical key via _normalize_header (spaces/case removed).
_SDL_ALIASES = {
    "UNIQUEKEY": "unique_key",
    "PRODUCTTITLE": "title",
    "PRODUCTDESCRIPTION": "description",
    "STYLE#": "style",
    "STYLE": "style",
    "COLORNAME": "color_name",
    "SANMARMAINFRAMECOLOR": "mainframe_color",
    "SIZE": "size",
    "PIECEPRICE": "piece_price",
    "CASEPRICE": "case_price",
    "CASESIZE": "case_size",
    "PIECEWEIGHT": "piece_weight",
    "INVENTORYKEY": "inventory_key",
    "SIZEINDEX": "size_index",
    "MILL": "brand",
    "CATEGORYNAME": "category",
    "SUBCATEGORYNAME": "subcategory",
    "PRODUCTSTATUS": "product_status",
    "MSRP": "msrp",
    # SanMar has spelled the MAP column a few ways across SDL/EPDD revisions;
    # accept the known variants so a rename doesn't silently drop MAP.
    "MAPPRICING": "map_price",
    "MAPPRICE": "map_price",
    "MAP": "map_price",
    "GTIN": "gtin",
    "QTY": "available_qty",
    "FRONTMODELIMAGEURL": "front_model_url",
    "BACKMODELIMAGEURL": "back_model_url",
    "FRONTFLATIMAGEURL": "front_flat_url",
    "BACKFLATIMAGEURL": "back_flat_url",
    "COLORSWATCHIMAGE": "color_swatch_url",
}


def _index_headers(fieldnames: Iterable[str]) -> dict[str, str]:
    """Map model attribute name -> the actual CSV header string in this file."""
    resolved: dict[str, str] = {}
    for raw in fieldnames:
        canon = _normalize_header(raw)
        attr = _SDL_ALIASES.get(canon)
        if attr and attr not in resolved:
            resolved[attr] = raw
    return resolved


# ── SDL_N / EPDD reader ──────────────────────────────────────────────────────
def iter_sdl_rows(path: str | Path) -> Iterator[dict[str, str]]:
    """Yield raw dict rows from an SDL_N / EPDD CSV, keyed by model attribute.

    Each yielded dict has normalized keys (``unique_key``, ``style``,
    ``piece_price``, ``front_model_url``, ...) regardless of header spelling.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            log.warning("No header row found in %s", path)
            return
        header_map = _index_headers(reader.fieldnames)
        missing = {"unique_key", "style"} - header_map.keys()
        if missing:
            raise ValueError(
                f"{path.name} is missing required column(s): {sorted(missing)}. "
                f"Found headers: {reader.fieldnames}"
            )
        for raw_row in reader:
            yield {attr: _clean(raw_row.get(col)) for attr, col in header_map.items()}


def parse_styles(path: str | Path) -> list[StyleRecord]:
    """Parse an SDL_N / EPDD file into de-duplicated :class:`StyleRecord` list.

    SKUs are grouped by ``style``; duplicate unique keys (EPDD repeats them per
    category/subcategory) collapse to the first occurrence. Image URLs are
    collected per color.
    """
    styles: dict[str, _StyleBuilder] = {}
    seen_unique_keys: set[str] = set()

    for row in iter_sdl_rows(path):
        unique_key = row.get("unique_key", "")
        style_no = row.get("style", "")
        if not unique_key or not style_no:
            continue
        builder = styles.get(style_no)
        if builder is None:
            builder = _StyleBuilder(style_no, row)
            styles[style_no] = builder
        # Image URLs are defined per color; record even on duplicate SKU rows.
        builder.add_color_images(row)
        if unique_key in seen_unique_keys:
            continue
        seen_unique_keys.add(unique_key)
        builder.add_sku(row)

    return [b.build() for b in styles.values()]


class _StyleBuilder:
    """Accumulates SKUs + per-color images for a single style during parsing."""

    def __init__(self, style: str, first_row: dict[str, str]) -> None:
        self.style = style
        self.title = first_row.get("title", "")
        self.description = first_row.get("description", "")
        self.brand = first_row.get("brand", "")
        self.category = first_row.get("category", "")
        self.subcategory = first_row.get("subcategory", "")
        self.product_status = first_row.get("product_status", "")
        self._skus: list[SkuRecord] = []
        self._images: dict[str, ColorImages] = {}

    def add_sku(self, row: dict[str, str]) -> None:
        self._skus.append(
            SkuRecord(
                unique_key=row.get("unique_key", ""),
                inventory_key=row.get("inventory_key", ""),
                size_index=row.get("size_index", ""),
                style=self.style,
                color_name=row.get("color_name", ""),
                mainframe_color=row.get("mainframe_color", ""),
                size=row.get("size", ""),
                description=row.get("description", self.description),
                piece_price=_decimal(row.get("piece_price")),
                case_price=_decimal(row.get("case_price")),
                case_size=_int(row.get("case_size")),
                piece_weight=_decimal(row.get("piece_weight")),
                msrp=_decimal(row.get("msrp")),
                map_price=_decimal(row.get("map_price")),
                gtin=row.get("gtin", ""),
                product_status=row.get("product_status", self.product_status),
                available_qty=_int(row.get("available_qty")),
            )
        )

    def add_color_images(self, row: dict[str, str]) -> None:
        color = row.get("color_name", "")
        if not color:
            return
        front = row.get("front_model_url", "")
        back = row.get("back_model_url", "")
        front_flat = row.get("front_flat_url", "")
        back_flat = row.get("back_flat_url", "")
        swatch = row.get("color_swatch_url", "")
        if not any([front, back, front_flat, back_flat, swatch]):
            return
        existing = self._images.get(color)
        # Merge: keep the first non-empty URL seen for each slot.
        self._images[color] = ColorImages(
            color_name=color,
            front_model_url=front or (existing.front_model_url if existing else ""),
            back_model_url=back or (existing.back_model_url if existing else ""),
            front_flat_url=front_flat or (existing.front_flat_url if existing else ""),
            back_flat_url=back_flat or (existing.back_flat_url if existing else ""),
            color_swatch_url=swatch or (existing.color_swatch_url if existing else ""),
        )

    def build(self) -> StyleRecord:
        return StyleRecord(
            style=self.style,
            title=self.title,
            description=self.description,
            brand=self.brand,
            category=self.category,
            subcategory=self.subcategory,
            product_status=self.product_status,
            skus=self._skus,
            images_by_color=self._images,
        )


# ── sanmar_dip.txt reader ────────────────────────────────────────────────────
def parse_inventory(path: str | Path) -> list[InventoryRecord]:
    """Parse ``sanmar_dip.txt`` into one :class:`InventoryRecord` per SKU.

    The file has one row per warehouse, so rows are grouped by ``unique_key``
    and warehouse quantities are aggregated. Pricing/sale fields are taken from
    the first row seen for each SKU. SanMar's live export begins with a
    column-header line, which is skipped.
    """
    path = Path(path)
    builders: dict[str, _InventoryBuilder] = {}

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="|")
        for fields in reader:
            if len(fields) < C.DIP_MIN_COLUMNS:
                continue
            # Skip the column-header row SanMar includes on line 1 of the dip
            # export (and any malformed row): a real data row's warehouse number
            # is numeric, whereas the header carries the literal "whse_no".
            whse_no = _clean(fields[C.DIP_WHSE_NO])
            if whse_no and not whse_no.isdigit():
                continue
            unique_key = _clean(fields[C.DIP_UNIQUE_KEY])
            if not unique_key:
                # Fall back to inventory_key+size_index if unique_key is blank.
                unique_key = (
                    _clean(fields[C.DIP_INVENTORY_KEY]) + _clean(fields[C.DIP_SIZE_INDEX])
                )
            if not unique_key:
                continue
            builder = builders.get(unique_key)
            if builder is None:
                builder = _InventoryBuilder(unique_key, fields)
                builders[unique_key] = builder
            builder.add_warehouse(fields)

    return [b.build() for b in builders.values()]


class _InventoryBuilder:
    def __init__(self, unique_key: str, fields: list[str]) -> None:
        self.unique_key = unique_key
        self.inventory_key = _clean(fields[C.DIP_INVENTORY_KEY])
        self.size_index = _clean(fields[C.DIP_SIZE_INDEX])
        self.style = _clean(fields[C.DIP_CATALOG_NO])
        self.color = _clean(fields[C.DIP_CATALOG_COLOR])
        self.size = _clean(fields[C.DIP_SIZE])
        self.piece_price = _decimal(fields[C.DIP_PIECE_PRICE])
        self.case_price = _decimal(fields[C.DIP_CASE_PRICE])
        self.each_sale_price = _decimal(fields[C.DIP_EACH_SALE_PRICE])
        self.case_sale_price = _decimal(fields[C.DIP_CASE_SALE_PRICE])
        self.sale_start = _clean(fields[C.DIP_SALE_START_DATETIME])
        self.sale_end = _clean(fields[C.DIP_SALE_END_DATETIME])
        self.discontinued_code = _clean(fields[C.DIP_DISCONTINUED_CODE])
        self._warehouses: dict[str, int] = {}

    def add_warehouse(self, fields: list[str]) -> None:
        whse = _clean(fields[C.DIP_WHSE_NO])
        if not whse or not whse.isdigit():
            return
        qty = _int(fields[C.DIP_QUANTITY]) or 0
        self._warehouses[whse] = self._warehouses.get(whse, 0) + qty

    def build(self) -> InventoryRecord:
        warehouses = [
            WarehouseQty(
                warehouse_no=whse,
                warehouse_label=C.WAREHOUSES.get(whse, f"Warehouse {whse}"),
                quantity=qty,
            )
            for whse, qty in sorted(self._warehouses.items(), key=lambda kv: int(kv[0]))
        ]
        return InventoryRecord(
            unique_key=self.unique_key,
            inventory_key=self.inventory_key,
            size_index=self.size_index,
            style=self.style,
            color=self.color,
            size=self.size,
            warehouses=warehouses,
            piece_price=self.piece_price,
            case_price=self.case_price,
            each_sale_price=self.each_sale_price,
            case_sale_price=self.case_sale_price,
            sale_start=self.sale_start,
            sale_end=self.sale_end,
            discontinued_code=self.discontinued_code,
        )
