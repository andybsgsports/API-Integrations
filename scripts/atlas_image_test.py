"""Single-item live test: what does custitem_atlas_item_image accept?

Targets one real item (PC450-White-Small by default) and tries, in order:
1. A plain URL string (cheap — the SanMar-hosted image URL directly).
2. If rejected, uploads the image bytes into the File Cabinet (reusing the
   existing ImageUploader) and writes a Document reference ({"id": file_id}).

Reads the value back via SuiteQL after each attempt to confirm what actually
landed. This is a single, reversible, targeted write — not a bulk change.
"""

from __future__ import annotations

import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.netsuite.soap_files import upload_from_url_soap
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp

FIELD = "custitem_atlas_item_image"
TARGET_ITEMID = os.environ.get("ATLAS_TEST_ITEMID", "PC450-White-Small")


def _resolve_image_url(cfg, itemid: str) -> str | None:
    """Source the color's image directly from the feed — independent of
    whether the bulk field update has run yet."""
    style_token, color = itemid.split("-", 1)
    color = color.rsplit("-", 1)[0]  # strip the trailing -Size
    path = Path(cfg.sftp.download_dir) / C.FILE_SDL_N
    if not path.exists():
        path = SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)
    for style in parse_styles(path):
        if style.style == style_token:
            images = style.images_by_color.get(color)
            return images.primary_url() if images else None
    return None


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    rows = client.suiteql(
        f"SELECT id, itemid FROM item WHERE itemid = '{_sql_escape(TARGET_ITEMID)}'"
    )
    if not rows:
        print(f"item {TARGET_ITEMID} not found")
        return 1
    item_id = str(rows[0]["id"])
    print(f"target item {TARGET_ITEMID} -> internal id {item_id}")

    image_url = _resolve_image_url(cfg, TARGET_ITEMID)
    print(f"source image URL (resolved from feed): {image_url}")
    if not image_url:
        print("no image found in the feed for this style/color")
        return 1

    print(f"\n--- attempt 1: plain URL string into {FIELD} ---")
    try:
        client.update_record("inventoryItem", item_id, {FIELD: image_url})
        print("PATCH accepted (HTTP 200/204)")
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "payload", "")
        print(f"PATCH rejected: {str(exc)[:150]}")
        print(f"detail: {str(detail)[:500]}")
    readback = client.suiteql(f"SELECT {FIELD} FROM item WHERE id = '{item_id}'")
    val = readback[0].get(FIELD) if readback else None
    print(f"readback after attempt 1: {val!r}")
    if val:
        print("\nRESULT: plain URL string works.")
        return 0

    print(f"\n--- attempt 2: SOAP-upload bytes + Document reference into {FIELD} ---")
    folder_id = os.environ.get("NETSUITE_IMAGE_FOLDER_ID", "")
    if not folder_id:
        print("NETSUITE_IMAGE_FOLDER_ID not set — cannot test the upload path")
        return 1
    try:
        uploaded = upload_from_url_soap(
            cfg.netsuite, image_url, f"{TARGET_ITEMID}.jpg", folder_id
        )
    except Exception as exc:  # noqa: BLE001
        print(f"SOAP upload failed: {str(exc)[:800]}")
        return 1
    print(f"uploaded -> File Cabinet id {uploaded.file_id}")

    for shape_name, shape in (
        ("id-string", {"id": uploaded.file_id}),
        ("internalId-key", {"internalId": uploaded.file_id}),
    ):
        try:
            client.update_record("inventoryItem", item_id, {FIELD: shape})
            print(f"PATCH accepted with shape [{shape_name}] (HTTP 200/204)")
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "payload", "")
            print(f"PATCH rejected with shape [{shape_name}]: {str(exc)[:150]}")
            print(f"detail: {str(detail)[:500]}")
            continue
        readback = client.suiteql(f"SELECT {FIELD} FROM item WHERE id = '{item_id}'")
        val = readback[0].get(FIELD) if readback else None
        print(f"readback after shape [{shape_name}]: {val!r}")
        if val:
            print(f"\nRESULT: Document reference works with shape {shape_name}: {shape}")
            return 0

    print("\nRESULT: file uploaded (id "
          f"{uploaded.file_id}) but no attach shape worked — needs manual inspection.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
