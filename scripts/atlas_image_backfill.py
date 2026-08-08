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

import csv
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from concurrent_writes import write_records
from dcos_backfill import SUPPLIERS as DCOS_SUPPLIERS
from dcos_backfill import get_sellable_styles, get_style_images
from run_status import exit_code

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
# UPC -> Champro product image, from the user-supplied champrosports.com export
# (data/champro_images.csv). Per-color images, matched to items by barcode.
CHAMPRO_IMAGE_CSV = Path(__file__).resolve().parents[1] / "data" / "champro_images.csv"


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


def champro_csv_images() -> dict[str, str]:
    """UPC -> Champro image from the champrosports.com export. Keyed under
    several barcode paddings (UPC-12 / EAN-13 / GTIN-14, and zero-stripped) so
    it matches whatever form NetSuite's upccode is stored in."""
    out: dict[str, str] = {}
    if not CHAMPRO_IMAGE_CSV.exists():
        return out
    with CHAMPRO_IMAGE_CSV.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            upc = (row.get("upc") or "").strip()
            url = (row.get("image_url") or "").strip()
            if not upc or not url:
                continue
            core = upc.lstrip("0") or upc
            for key in {upc, core, core.zfill(12), core.zfill(13), core.zfill(14)}:
                out.setdefault(key, url)
    return out


SOURCES = [  # ranking order: (item key field, loader)
    ("custitem_sanmar_unique_key", sanmar_images),
    ("custitem_mtec_item_sku", momentec_images),
    ("custitem_ss_sku", ss_images),
    # Champro barcode-matched images rank above the DC OneSource feed image
    # (below) so items get the per-color champrosports.com photo, not the
    # single product-level feed image.
    ("upccode", champro_csv_images),
]

# DC OneSource has no Media Content service, but its Product Data response
# carries primaryImageUrl (confirmed for every vendor). Images key by partId
# (custitem_<prefix>_part_id).
#
# "product" style_source suppliers: the stored style IS the feed productId, so
# look up only the styles present in NetSuite. (item field, base, style field)
DCOS_IMAGE_SUPPLIERS: list[tuple[str, str, str]] = [
    ("custitem_ua_part_id", "https://api.dc-onesource.com/xml/UNDERARMOR",
     "custitem_ua_style"),
]
for _k, _v in DCOS_SUPPLIERS.items():
    if _v.get("style_source") == "product":
        DCOS_IMAGE_SUPPLIERS.append((
            f"custitem_{_k}_part_id",
            f"https://api.dc-onesource.com/xml/{_v['slug']}",
            f"custitem_{_k}_style",
        ))

# "part" style_source suppliers (usb, tck): the stored style isn't the feed
# productId, so walk the feed's sellable productIds, pull each product's
# per-part primaryImageUrl, and keep the parts present in NetSuite (early-stop
# once all are covered). (item key field, endpoint base)
DCOS_PART_IMAGE_SUPPLIERS: list[tuple[str, str]] = [
    (f"custitem_{_k}_part_id", f"https://api.dc-onesource.com/xml/{_v['slug']}")
    for _k, _v in DCOS_SUPPLIERS.items() if _v.get("style_source") == "part"
]


def dcos_image_map(client, base: str, style_field: str, part_field: str,
                   key_id: str, key_pw: str) -> dict[str, str]:
    """partId -> primaryImageUrl for every DCOS style present in NetSuite."""
    if not key_id or not key_pw:
        return {}
    try:
        rows = client.suiteql(
            f"SELECT DISTINCT {style_field} AS s FROM item "
            f"WHERE {part_field} IS NOT NULL AND {style_field} IS NOT NULL"
        )
    except Exception:  # noqa: BLE001 - supplier field may not exist yet
        return {}
    out: dict[str, str] = {}
    for r in rows:
        style = str(r.get("s") or "").strip()
        if not style:
            continue
        try:
            out.update(get_style_images(base, key_id, key_pw, style))
        except Exception:  # noqa: BLE001 - one bad style shouldn't sink the rest
            continue
    return out


def dcos_image_map_by_part(client, base: str, part_field: str,
                           key_id: str, key_pw: str) -> dict[str, str]:
    """partId -> primaryImageUrl for a "part"-source vendor (usb, tck), whose
    stored style isn't the feed productId. Walk the feed's sellable products,
    keeping images for the parts present in NetSuite; stop once all are found."""
    if not key_id or not key_pw:
        return {}
    try:
        rows = client.suiteql(
            f"SELECT DISTINCT {part_field} AS p FROM item WHERE {part_field} IS NOT NULL"
        )
    except Exception:  # noqa: BLE001 - supplier field may not exist yet
        return {}
    targets = {str(r.get("p") or "").strip() for r in rows}
    targets.discard("")
    if not targets:
        return {}
    try:
        styles = get_sellable_styles(base, key_id, key_pw)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for style in styles:
        try:
            imgs = get_style_images(base, key_id, key_pw, style)
        except Exception:  # noqa: BLE001 - one bad style shouldn't sink the rest
            continue
        for pid, url in imgs.items():
            if pid in targets:
                out[pid] = url
        if len(out) >= len(targets):
            break  # every in-NetSuite part now has an image
    return out


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

    key_id = os.environ.get("DCOS_KEY_ID", "")
    key_pw = os.environ.get("DCOS_KEY_PASSWORD", "")
    # ranking: supplier feeds first, then DC OneSource primaryImageUrl.
    ranked_fields = (
        [f for f, _ in SOURCES]
        + [f for f, _, _ in DCOS_IMAGE_SUPPLIERS]
        + [f for f, _ in DCOS_PART_IMAGE_SUPPLIERS]
    )
    key_cols = ", ".join(ranked_fields)
    where = " OR ".join(f"{f} IS NOT NULL" for f in ranked_fields)
    items = client.suiteql(f"SELECT id, {key_cols}, {FIELD} FROM item WHERE {where}")
    print(f"supplier-matched items: {len(items):,}")

    # Every source is OPTIONAL: an unavailable feed (the S&S products.json
    # snapshot only exists on S&S runs; a DCOS credential can be absent) must
    # cost that source's images, not the run -- items whose best source was
    # skipped just stay imageless until a run where it's available. The
    # unconditional loader() here crashed the first in-pipeline run
    # (2026-08-05, run 31046102022) on the missing S&S snapshot.
    url_by_field: dict[str, dict[str, str]] = {}
    for field, loader in SOURCES:
        try:
            url_by_field[field] = loader()
            print(f"{field}: images for {len(url_by_field[field]):,} feed SKUs")
        except Exception as exc:  # noqa: BLE001
            url_by_field[field] = {}
            print(f"{field}: source unavailable, skipping ({str(exc)[:100]})")
    for field, base, style_field in DCOS_IMAGE_SUPPLIERS:
        try:
            url_by_field[field] = dcos_image_map(
                client, base, style_field, field, key_id, key_pw)
            print(f"{field}: images for {len(url_by_field[field]):,} DCOS parts")
        except Exception as exc:  # noqa: BLE001
            url_by_field[field] = {}
            print(f"{field}: source unavailable, skipping ({str(exc)[:100]})")
    for field, base in DCOS_PART_IMAGE_SUPPLIERS:
        try:
            url_by_field[field] = dcos_image_map_by_part(
                client, base, field, key_id, key_pw)
            print(f"{field}: images for {len(url_by_field[field]):,} DCOS parts (by part)")
        except Exception as exc:  # noqa: BLE001
            url_by_field[field] = {}
            print(f"{field}: source unavailable, skipping ({str(exc)[:100]})")

    folder_id = _resolve_folder_id(client, cfg, allow_write)
    print(f"using File Cabinet folder id {folder_id}")

    file_id_by_url: dict[str, str] = {}
    considered = written = unchanged = nourl = failures = upload_failures = 0
    samples: dict[str, int] = {}
    write_jobs: list[tuple[str, dict]] = []
    for row in items:
        rid = str(row["id"])
        if str(row.get(FIELD) or "").strip():
            unchanged += 1
            continue
        want_url = src = None
        for field in ranked_fields:  # ranking order
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
        write_jobs.append((rid, {FIELD: file_id}))

    # PATCHes are independent per item -- issue them with bounded concurrency
    # instead of one-at-a-time, which is throttle-bound and can run for hours.
    _fail_shown = [0]
    _fail_other = [0]

    def _on_err(rid: str, exc: Exception) -> None:
        _fail_shown[0] += 1
        if "429" not in str(exc):
            _fail_other[0] += 1
        if _fail_shown[0] <= 10:
            detail = getattr(exc, "payload", "")
            print(f"  FAILED item {rid}: {str(exc)[:100]} :: {str(detail)[:200]}")

    w, f = write_records(client, "inventoryItem", write_jobs, on_error=_on_err)
    written += w
    failures += f

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(
        f"\natlas image backfill: {verb} {written} item(s); "
        f"distinct images uploaded: {len(file_id_by_url)}; unchanged: {unchanged}; "
        f"no feed image: {nourl}; upload failures: {upload_failures}; "
        f"write failures: {failures}"
    )
    # An upload failure is never transient throttling (the File Cabinet SOAP
    # path has its own error modes), so it stays fatal on its own.
    if upload_failures:
        return 1
    return exit_code("atlas image backfill", failures, _fail_other[0],
                     written + failures)


if __name__ == "__main__":
    raise SystemExit(main())
