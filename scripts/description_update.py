"""Refresh item copy (display name, sales & purchase descriptions) from the
highest-ranked matched supplier's feed. Runs on CI.

Items already carry their supplier keys (written by the back-fills), so this
joins item -> feed copy directly:

    SanMar (custitem_sanmar_unique_key)  >  Momentec (custitem_mtec_item_sku)
    >  S&S (custitem_ss_sku)

Composition per item (variant-aware):
    Display Name         "<Product name> - <Color> - <Size>"
    Sales Description    supplier marketing copy (HTML entities unescaped)
    Purchase Description "<Brand/Supplier> <Style> <Product name> - <Color>/<Size>"

Diff-aware; honors ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes. The
dry run prints before/after samples for review.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

from momentec_netsuite.config import get_config as mtec_config
from momentec_netsuite.feeds import parse_product_data
from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp
from ss_activewear_netsuite.config import get_config as ss_config

MAXLEN = {"displayName": 300, "salesDescription": 4000, "purchaseDescription": 4000}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


_STATUS_PREFIX = re.compile(r"^(DISCONTINUED|CLOSEOUT|NEW)\b[\s:–-]*", re.I)


def _polish_name(name: str, style: str) -> str:
    """Feed titles arrive with status prefixes, trailing style numbers, and
    sometimes ALL CAPS; normalize to a clean product title."""
    name = _STATUS_PREFIX.sub("", _clean(name))
    if style:
        name = re.sub(rf"[\s.,-]*{re.escape(style)}[\s.]*$", "", name, flags=re.I)
    name = name.strip(" .,-")
    if name.isupper():
        name = name.title()
    return name


def _polish_desc(desc: str) -> str:
    """Insert sentence breaks at run-together boundaries (4+ lowercase then a
    capitalized word) — SanMar concatenates bullet sentences without spaces."""
    return re.sub(r"(?<=[a-z]{6})(?=[A-Z][a-z]{3,})", ". ", desc)


def _copy(name: str, brand: str, style: str, color: str, size: str, desc: str) -> dict:
    """Plain product title on all three copy fields — no color/size suffix,
    no style-number prefix. Variant info already lives in the item name
    (STYLE-COLOR-SIZE) and the matrix color/size fields, so repeating it in
    the customer-facing copy is redundant."""
    name = _polish_name(name, style)
    out = {"displayName": name, "salesDescription": name, "purchaseDescription": name}
    return {k: v[: MAXLEN[k]] for k, v in out.items() if v}


def sanmar_copy() -> dict[str, dict]:
    cfg = ns_config()
    path = Path(cfg.sftp.download_dir) / C.FILE_SDL_N
    if not path.exists():
        path = SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)
    out: dict[str, dict] = {}
    for style in parse_styles(path):
        for sku in style.skus:
            out[sku.unique_key] = _copy(
                style.title or sku.description, style.brand, sku.style,
                sku.color_name, sku.size, sku.description or style.description,
            )
    return out


def momentec_copy() -> dict[str, dict]:
    cfg = mtec_config()
    dl = Path(cfg.download_dir)

    def fetch(url: str, dest: Path) -> Path:
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=300) as resp, dest.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
        return dest

    styles = parse_product_data([
        fetch(cfg.products_url, dl / "product-data-std-all.csv"),
        fetch(cfg.sublimation_url, dl / "sublimation-product-data-std-all.csv"),
    ])
    out: dict[str, dict] = {}
    for s in styles:
        for k in s.skus:
            out[k.item_sku] = _copy(
                k.name, "", k.parent_sku or k.item_sku.split(".")[0],
                k.color.title(), k.size, k.description,
            )
    return out


def ss_copy() -> dict[str, dict]:
    products_file = Path(ss_config().download_dir) / "products.json"
    # SKU-level style names are thin ("Colortone"); the style-level title is
    # the real product name ("Multi-Color Tie-Dyed T-Shirt"). /Styles returns
    # the whole list in one call; fall back to style names if it fails.
    titles: dict[str, str] = {}
    try:
        from ss_activewear_netsuite.ss_activewear.client import SsClient

        for s in SsClient(ss_config().ss_api).iter_styles():
            if s.title:
                titles[str(s.style_id)] = s.title
        print(f"S&S style titles fetched: {len(titles):,}")
    except Exception as exc:  # noqa: BLE001
        print(f"(S&S style titles unavailable, using style names: {str(exc)[:80]})")

    out: dict[str, dict] = {}
    for p in json.loads(products_file.read_text(encoding="utf-8")):
        brand = p.get("brand_name") or ""
        name = titles.get(str(p.get("style_id") or "")) or p.get("style_name") or ""
        if brand and not name.lower().startswith(brand.lower()):
            name = f"{brand} {name}".strip()
        out[p.get("sku") or ""] = _copy(
            name, brand,
            p.get("style_name") or "", p.get("color_name") or "", p.get("size_name") or "",
            p.get("description") or "",
        )
    return out


SOURCES = [  # ranking order: (item key field, loader)
    ("custitem_sanmar_unique_key", sanmar_copy),
    ("custitem_mtec_item_sku", momentec_copy),
    ("custitem_ss_sku", ss_copy),
]


def main() -> int:
    allow_write = not ns_config().sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(ns_config().netsuite)

    key_cols = ", ".join(f for f, _ in SOURCES)
    where = " OR ".join(f"{f} IS NOT NULL" for f, _ in SOURCES)
    # SuiteQL column names differ from REST field names; probe progressively.
    items = None
    for extra in (
        "displayname, description, purchasedescription",
        "displayname, description",
        "displayname",
    ):
        try:
            items = client.suiteql(
                f"SELECT id, {key_cols}, {extra} FROM item WHERE {where}"
            )
            print(f"queried columns: {extra}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"column set rejected ({extra}): {str(exc)[:80]}")
    if items is None:
        raise SystemExit("no column set accepted")
    print(f"supplier-matched items: {len(items):,}")

    copy_by_field: dict[str, dict[str, dict]] = {}
    for field, loader in SOURCES:
        copy_by_field[field] = loader()
        print(f"{field}: copy for {len(copy_by_field[field]):,} feed SKUs")

    considered = written = unchanged = nocopy = failures = 0
    samples: dict[str, int] = {}
    for row in items:
        rid = str(row["id"])
        want = None
        for field, _ in SOURCES:  # ranking order
            key = str(row.get(field) or "").strip()
            if key:
                want = copy_by_field[field].get(key)
                if want:
                    break
        if not want:
            nocopy += 1
            continue
        current = {
            "displayName": str(row.get("displayname") or ""),
            "salesDescription": str(row.get("description") or ""),
            "purchaseDescription": str(row.get("purchasedescription") or ""),
        }
        body = {k: v for k, v in want.items() if _clean(current[k]) != _clean(v)}
        if not body:
            unchanged += 1
            continue
        if max_items and considered >= max_items:
            continue
        considered += 1
        src = next(
            (f for f, _ in SOURCES if str(row.get(f) or "").strip()
             and copy_by_field[f].get(str(row.get(f) or "").strip())),
            "?",
        )
        if samples.get(src, 0) < 3:
            samples[src] = samples.get(src, 0) + 1
            print(f"  sample item {rid} [{src}]:")
            for k, v in body.items():
                print(f"    {k}: {current[k][:70]!r} -> {v[:70]!r}")
        if not allow_write:
            written += 1
            continue
        try:
            client.update_record("inventoryItem", rid, body)
            written += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if failures <= 10:
                detail = getattr(exc, "payload", "")
                print(f"  FAILED item {rid}: {str(exc)[:100]} :: {str(detail)[:200]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\ndescription update: {verb} {written} item(s); unchanged: {unchanged}; "
          f"no feed copy: {nocopy}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
