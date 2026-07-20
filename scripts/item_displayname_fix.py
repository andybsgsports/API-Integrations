"""Normalise the Display Name / Purchase Description across a style (item writes).

When a style is carried by more than one vendor, a feed can set a child's
Display Name to its own product title -- e.g. 695HBM's S&S-matched sizes read
"Russell Athletic Unisex Dri Power(R) Hooded Sweatshirt" while the Momentec-only
ones read "Dri-Power(R) Fleece Hoodie". ``data/displayname_overrides.csv``
(style, displayname) sets one agreed name across every item of that style whose
name differs. Diff-aware; honours ``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

ROOT = Path(__file__).resolve().parents[1]


def load_overrides() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with (ROOT / "data" / "displayname_overrides.csv").open(
        encoding="utf-8-sig", newline=""
    ) as fh:
        for r in csv.DictReader(fh):
            st = (r.get("style") or "").strip()
            dn = (r.get("displayname") or "").strip()
            if st and dn:
                rows.append((st, dn))
    return rows


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    considered = written = failures = 0
    for style, target in load_overrides():
        rows = client.suiteql(
            "SELECT id, itemid, displayname, purchasedescription "
            f"FROM item WHERE vendorname = '{_sql_escape(style)}'"
        )
        n_diff = 0
        for r in rows:
            body: dict = {}
            if str(r.get("displayname") or "") != target:
                body["displayname"] = target
            if str(r.get("purchasedescription") or "") != target:
                body["purchasedescription"] = target
            if not body:
                continue
            n_diff += 1
            if max_items and considered >= max_items:
                continue
            considered += 1
            if considered <= 12:
                print(f"  {r.get('itemid')}: set {list(body)} -> {target!r}")
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", str(r["id"]), body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures <= 10:
                    detail = getattr(exc, "payload", "")
                    print(f"  FAILED item {r['id']}: {str(exc)[:150]} :: {str(detail)[:300]}")
        print(f"style {style!r}: {n_diff} item(s) differ from {target!r}")

    verb = "set" if allow_write else "WOULD set (dry run)"
    print(f"\ndisplay name normalise: {verb} {written} item(s); considered: "
          f"{considered}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
