"""Find the exact field name for the "Display in Web Site" checkbox, and check
whether it's set consistently with custitem_atlas_item_image (the real item
picture field). Read-only; a handful of lightweight calls (no bulk reads).

Primary approach: probe a shortlist of plausible SuiteQL column names with the
same safe pattern used throughout this codebase (``SELECT col FROM item WHERE
rownum <= 1`` -- a bad name errors, it doesn't crash the run). This is more
reliable than parsing the REST metadata-catalog swagger doc, whose exact JSON
shape isn't guaranteed (that path returned 0 properties on a first attempt).

The metadata-catalog dump is kept as a secondary, best-effort source of
candidate names, but a probe miss there is not treated as a failure -- this
script always exits 0; it's a diagnostic, not a check that should redden the PR.
"""

from __future__ import annotations

import json

import requests

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

IMAGE_FIELD = "custitem_atlas_item_image"

# Plausible SuiteQL/REST field ids for the "Display in Web Site" checkbox,
# ordered by likelihood based on NetSuite's usual naming conventions.
CANDIDATE_COLUMNS = [
    "displayinwebsite",
    "isonline",
    "webdisplay",
    "showinwebsite",
    "displayinsite",
    "webstoredisplay",
    "isavailableonwebstore",
    "matchbilltoreceipt",  # sanity check: a known-real column, to confirm probing works at all
]


def _projects(client: NetSuiteClient, col: str) -> bool:
    try:
        client.suiteql(f"SELECT {col} FROM item WHERE rownum <= 1")
        return True
    except Exception:  # noqa: BLE001 - column absent/unqueryable
        return False


def main() -> int:
    cfg = get_config()
    client = NetSuiteClient(cfg.netsuite)

    print("=== probing candidate SuiteQL column names ===")
    found: list[str] = []
    for col in CANDIDATE_COLUMNS:
        ok = _projects(client, col)
        print(f"  {'HAVE' if ok else 'miss'}  {col}")
        if ok:
            found.append(col)

    print("\n=== REST metadata catalog (best-effort, secondary source) ===")
    url = f"{cfg.netsuite.rest_base}/services/rest/record/v1/metadata-catalog/inventoryItem"
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
            print(f"  top-level keys: {sorted(doc.keys())}")
            # Try both Swagger 2.0 and OpenAPI 3 shapes.
            swagger_defs = doc.get("definitions", {}).get("inventoryItem", {})
            openapi_defs = doc.get("components", {}).get("schemas", {}).get("inventoryItem", {})
            props = swagger_defs.get("properties") or openapi_defs.get("properties") or {}
            print(f"  {len(props)} properties found at the expected paths")
            keywords = ("web", "online", "display", "site")
            hits = [n for n in props if any(k in n.lower() for k in keywords)]
            for name in hits:
                print(f"  MATCH {name}: {json.dumps(props[name])}")
        else:
            print(f"  body (first 300 chars): {resp.text[:300]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  metadata request failed: {str(exc)[:200]}")

    if not found:
        print("\nno candidate column projects -- field name needs a manual look in the "
              "NetSuite UI (Customization > Lists, Records & Fields, or the item form "
              "itself via right-click > 'View Field ID' if available)")
        return 0

    print(f"\n=== sample values for {found}, cross-checked against {IMAGE_FIELD} ===")
    cols = ", ".join(found)
    try:
        rows = client.suiteql(
            f"SELECT id, itemid, {cols}, {IMAGE_FIELD} FROM item FETCH FIRST 10 ROWS ONLY"
        )
        for r in rows:
            has_image = bool(str(r.get(IMAGE_FIELD) or "").strip())
            vals = {c: r.get(c) for c in found}
            print(f"    {r.get('itemid')}: {vals}  has_image={has_image}")
    except Exception as exc:  # noqa: BLE001
        print(f"  sample query failed: {str(exc)[:200]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
