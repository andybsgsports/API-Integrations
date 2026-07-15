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

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.files import ImageUploader
from sanmar_netsuite.netsuite.repository import _sql_escape

FIELD = "custitem_atlas_item_image"
TARGET_ITEMID = os.environ.get("ATLAS_TEST_ITEMID", "PC450-White-Small")


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    rows = client.suiteql(
        f"SELECT id, itemid, custitem_sanmar_front_image_url AS img_url "
        f"FROM item WHERE itemid = '{_sql_escape(TARGET_ITEMID)}'"
    )
    if not rows:
        print(f"item {TARGET_ITEMID} not found")
        return 1
    item_id = str(rows[0]["id"])
    image_url = rows[0].get("img_url")
    print(f"target item {TARGET_ITEMID} -> internal id {item_id}")
    print(f"source image URL: {image_url}")
    if not image_url:
        print("no image URL on this item yet (run the SanMar field update first)")
        return 1

    print(f"\n--- attempt 1: plain URL string into {FIELD} ---")
    try:
        client.update_record("inventoryItem", item_id, {FIELD: image_url})
        print("PATCH accepted (HTTP 200/204)")
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", "")
        print(f"PATCH rejected: {str(exc)[:150]}")
        print(f"detail: {str(detail)[:500]}")
    readback = client.suiteql(f"SELECT {FIELD} FROM item WHERE id = '{item_id}'")
    val = readback[0].get(FIELD) if readback else None
    print(f"readback after attempt 1: {val!r}")
    if val:
        print("\nRESULT: plain URL string works.")
        return 0

    print(f"\n--- attempt 2: upload bytes + Document reference into {FIELD} ---")
    folder_id = os.environ.get("NETSUITE_IMAGE_FOLDER_ID", "")
    if not folder_id:
        print("NETSUITE_IMAGE_FOLDER_ID not set — cannot test the upload path")
        return 1
    uploader = ImageUploader(client, folder_id)
    uploaded = uploader.upload_from_url(image_url, f"{TARGET_ITEMID}.jpg")
    print(f"uploaded -> File Cabinet id {uploaded.file_id}")
    try:
        client.update_record(
            "inventoryItem", item_id, {FIELD: {"id": uploaded.file_id}}
        )
        print("PATCH accepted (HTTP 200/204)")
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", "")
        print(f"PATCH rejected: {str(exc)[:150]}")
        print(f"detail: {str(detail)[:500]}")
    readback = client.suiteql(f"SELECT {FIELD} FROM item WHERE id = '{item_id}'")
    val = readback[0].get(FIELD) if readback else None
    print(f"readback after attempt 2: {val!r}")
    if val:
        print("\nRESULT: Document/file reference works.")
        return 0

    print("\nRESULT: neither format worked — needs manual inspection in the UI.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
