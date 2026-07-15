"""Back-fill custitem_atlas_item_image (the real NetSuite Image-type field)
from the highest-ranked matched supplier's feed. Runs on CI.

Same ranked join as description_update.py:
    SanMar (custitem_sanmar_unique_key) > Momentec (custitem_mtec_item_sku)
    > S&S (custitem_ss_sku)

A color's image is uploaded to the File Cabinet once via SOAP (REST has no
'file' record type), and the resulting Document reference is written as a
*bare file-id string* — confirmed live: {"id": ...} and {"internalId": ...}
are both rejected with INVALID_VALUE, but the bare id round-trips. Every size
sharing that color gets the same file id, so the White image shows on every
White size, not just the one SKU that happens to carry the URL.

Diff-aware: items that already carry a value in custitem_atlas_item_image are
left alone (that's the back-fill signal — "images are not updated" means the
field is empty). Uploads are deduped by source URL within the run, so a
25-size style only uploads as many distinct images as it has colors. Honors
SYNC_DRY_RUN; UPDATE_MAX_ITEMS caps item writes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from momentec_netsuite.config import get_config as mtec_config
from momentec_netsuite.feeds import parse_product_data
from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.netsuite.soap_files import create_folder_soap, upload_from_url_soap
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp
from ss_activewear_netsuite.config import get_config as ss_config

FIELD = "custitem_atlas_item_image"
FOLDER_NAME = "Supplier Item Images"


def sanmar_images() -> dict[str, str]:
    cfg = ns_config()
    path = Path(cfg.sftp.download_dir) / C.FILE_SDL_N
    if not path.exists():
        path = SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)
    out: dict[str, str] = {}
    for style in parse_styles(path):
        for sku in style.skus:
            images = style.images_by_color.get(sku.color_name)
            url = images.primary_url() if images else None
            if url:
                out[sku.unique_key] = url
    return out


def momentec_images() -> dict[str, str]:
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
    out: dict[str, str] = {}
    for s in styles:
        for k in s.skus:
            if k.main_image_url:
                out[k.item_sku] = k.main_image_url
    return out


def _abs_ss_url(path: str) -> str:
    """S&S image fields carry relative CDN paths, not full URLs."""
    v = path.strip()
    if not v or v.startswith(("http://", "https://")):
        return v
    return "https://cdn.ssactivewear.com/" + v.lstrip("/")


def ss_images() -> dict[str, str]:
    products_file = Path(ss_config().download_dir) / "products.json"
    out: dict[str, str] = {}
    for p in json.loads(products_file.read_text(encoding="utf-8")):
        url = _abs_ss_url(p.get("front_image_url") or p.get("on_model_image_url") or "")
        sku = p.get("sku") or ""
        if url and sku:
            out[sku] = url
    return out


SOURCES = [  # ranking order: (item key field, loader)
    ("custitem_sanmar_unique_key", sanmar_images),
    ("custitem_mtec_item_sku", momentec_images),
    ("custitem_ss_sku", ss_images),
]


def _resolve_folder_id(client, cfg, allow_write: bool) -> str:
    env_id = os.environ.get("NETSUITE_IMAGE_FOLDER_ID", "")
    if env_id:
        return env_id
    rows = client.suiteql(
        f"SELECT id FROM mediaitemfolder WHERE name = '{_sql_escape(FOLDER_NAME)}'"
    )
    if rows:
        return str(rows[0]["id"])
    if not allow_write:
        return "0"  # dry run: no writes happen, id is only used for logging
    folder_id = create_folder_soap(cfg.netsuite, FOLDER_NAME)
    print(f"created File Cabinet folder '{FOLDER_NAME}' -> id {folder_id}")
    return folder_id


def main() -> int:
    cfg = ns_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    key_cols = ", ".join(f for f, _ in SOURCES)
    where = " OR ".join(f"{f} IS NOT NULL" for f, _ in SOURCES)
    items = client.suiteql(f"SELECT id, {key_cols}, {FIELD} FROM item WHERE {where}")
    print(f"supplier-matched items: {len(items):,}")

    url_by_field: dict[str, dict[str, str]] = {}
    for field, loader in SOURCES:
        url_by_field[field] = loader()
        print(f"{field}: images for {len(url_by_field[field]):,} feed SKUs")

    folder_id = _resolve_folder_id(client, cfg, allow_write)
    print(f"using File Cabinet folder id {folder_id}")

    file_id_by_url: dict[str, str] = {}
    considered = written = unchanged = nourl = failures = upload_failures = 0
    samples: dict[str, int] = {}
    for row in items:
        rid = str(row["id"])
        if str(row.get(FIELD) or "").strip():
            unchanged += 1
            continue
        want_url = src = None
        for field, _ in SOURCES:  # ranking order
            key = str(row.get(field) or "").strip()
            if key and url_by_field[field].get(key):
                want_url, src = url_by_field[field][key], field
                break
        if not want_url:
            nourl += 1
            continue
        if max_items and considered >= max_items:
            continue
        considered += 1

        file_id = file_id_by_url.get(want_url)
        if file_id is None:
            if not allow_write:
                file_id = "DRYRUN"
            else:
                try:
                    ext = Path(want_url.split("?")[0]).suffix or ".jpg"
                    uploaded = upload_from_url_soap(
                        cfg.netsuite, want_url, f"item_{rid}{ext}", folder_id
                    )
                    file_id = uploaded.file_id
                except Exception as exc:  # noqa: BLE001
                    upload_failures += 1
                    if upload_failures <= 10:
                        print(f"  UPLOAD FAILED for {want_url[:100]}: {str(exc)[:150]}")
                    continue
            file_id_by_url[want_url] = file_id

        if samples.get(src, 0) < 3:
            samples[src] = samples.get(src, 0) + 1
            print(f"  sample item {rid} [{src}]: {want_url[:90]} -> file {file_id}")

        if not allow_write:
            written += 1
            continue
        try:
            client.update_record("inventoryItem", rid, {FIELD: file_id})
            written += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if failures <= 10:
                detail = getattr(exc, "payload", "")
                print(f"  FAILED item {rid}: {str(exc)[:100]} :: {str(detail)[:200]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(
        f"\natlas image backfill: {verb} {written} item(s); "
        f"distinct images uploaded: {len(file_id_by_url)}; unchanged: {unchanged}; "
        f"no feed image: {nourl}; upload failures: {upload_failures}; "
        f"write failures: {failures}"
    )
    return 1 if (failures or upload_failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
