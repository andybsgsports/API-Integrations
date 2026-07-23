"""Find the exact field name for the "Display in Web Site" checkbox, and check
whether it's set consistently with custitem_atlas_item_image (the real item
picture field). Read-only; a handful of lightweight calls (no bulk reads).

1. REST metadata catalog: search the inventoryItem schema for any property
   whose name suggests "web site" / "online" display, so we target the exact
   field instead of guessing (guessing already cost a live-write failure once
   this session).
2. SuiteQL: once found, sample a few items with/without an Atlas image to see
   whether the flag already correlates with image presence.
"""

from __future__ import annotations

import json

import requests

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

IMAGE_FIELD = "custitem_atlas_item_image"
CANDIDATE_SUBSTRINGS = ("web", "online", "display", "site")


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    print("=== REST metadata catalog: inventoryItem properties matching "
          f"{CANDIDATE_SUBSTRINGS} ===")
    url = f"{cfg.netsuite.rest_base}/services/rest/record/v1/metadata-catalog/inventoryItem"
    candidates: list[str] = []
    try:
        resp = requests.get(
            url,
            auth=client._new_auth(),  # noqa: SLF001 - build a signer for this GET
            headers={"Accept": "application/swagger+json"},
            timeout=60,
        )
        print(f"  HTTP {resp.status_code}")
        if resp.status_code == 200:
            doc = resp.json()
            props = doc.get("definitions", {}).get("inventoryItem", {}).get("properties", {})
            print(f"  {len(props)} total properties")
            for name, schema in props.items():
                lname = name.lower()
                if any(s in lname for s in CANDIDATE_SUBSTRINGS):
                    candidates.append(name)
                    print(f"  MATCH {name}: {json.dumps(schema)}")
        else:
            print(f"  body (first 500 chars): {resp.text[:500]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  metadata request failed: {str(exc)[:200]}")

    if not candidates:
        print("\nno candidate field found in the schema -- needs manual lookup in NetSuite UI")
        return 1

    print(f"\n=== SuiteQL: sample values for each candidate, cross-checked against "
          f"{IMAGE_FIELD} ===")
    for col in candidates:
        try:
            rows = client.suiteql(
                f"SELECT id, itemid, {col}, {IMAGE_FIELD} FROM item "
                f"FETCH FIRST 8 ROWS ONLY"
            )
            print(f"\n  -- {col} --")
            for r in rows:
                has_image = bool(str(r.get(IMAGE_FIELD) or "").strip())
                print(f"    {r.get('itemid')}: {col}={r.get(col)!r}  "
                      f"has_image={has_image}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {col}: SuiteQL failed ({str(exc)[:150]})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
