"""Probe the schema of custitem_atlas_item_image (a pre-existing field, not
one we created) so the image writers can target it correctly. Read-only.

Two independent checks:
1. SuiteQL: is the field readable, and what does an existing value look like
   (a plain URL string vs a NetSuite file/document reference)?
2. REST metadata catalog: the OpenAPI schema for inventoryItem, which states
   the field's declared type (string/Document reference/etc).
"""

from __future__ import annotations

import json

import requests

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

FIELD = "custitem_atlas_item_image"


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    print(f"=== SuiteQL: sample values for {FIELD} ===")
    try:
        rows = client.suiteql(
            f"SELECT id, itemid, {FIELD} FROM item "
            f"WHERE {FIELD} IS NOT NULL FETCH FIRST 5 ROWS ONLY"
        )
        if not rows:
            print("  field exists but no items currently have a value")
        for r in rows:
            print(f"  item {r['id']} ({r.get('itemid')}): {r.get(FIELD)!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  SuiteQL failed: {str(exc)[:200]}")

    print(f"\n=== SuiteQL: total items with {FIELD} set ===")
    try:
        rows = client.suiteql(
            f"SELECT COUNT(*) AS n FROM item WHERE {FIELD} IS NOT NULL"
        )
        print(f"  {rows[0]['n']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {str(exc)[:200]}")

    print("\n=== spot-check: same color, all sizes, same file id? (PC450-White-*) ===")
    try:
        rows = client.suiteql(
            f"SELECT itemid, {FIELD} FROM item WHERE itemid LIKE 'PC450-White-%'"
        )
        for r in rows:
            print(f"  {r['itemid']}: {r.get(FIELD)!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {str(exc)[:200]}")

    print("\n=== REST metadata catalog: inventoryItem field schema ===")
    url = f"{cfg.netsuite.rest_base}/services/rest/record/v1/metadata-catalog/inventoryItem"
    try:
        resp = requests.get(
            url,
            auth=client._auth,  # noqa: SLF001 - reuse the OAuth1 signer, read-only GET
            headers={"Accept": "application/swagger+json"},
            timeout=60,
        )
        print(f"  HTTP {resp.status_code}")
        if resp.status_code == 200:
            doc = resp.json()
            props = (
                doc.get("definitions", {})
                .get("inventoryItem", {})
                .get("properties", {})
            )
            if FIELD in props:
                print(f"  schema for {FIELD}:")
                print(json.dumps(props[FIELD], indent=2))
            else:
                print(f"  {FIELD} not found in schema properties "
                      f"({len(props)} total properties)")
        else:
            print(f"  body (first 500 chars): {resp.text[:500]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  metadata request failed: {str(exc)[:200]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
