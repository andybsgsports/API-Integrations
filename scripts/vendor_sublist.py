"""Vendor sublist writer: rank suppliers on matched items (runs on CI).

For every item matched to at least one supplier (detected via the back-filled
key fields), ensures the item's Vendors sublist carries a line per matched
supplier and marks Preferred by the agreed ranking:

    SanMar (512)  >  Momentec/Augusta (264)  >  S&S (510)  >  UA (576)

Existing lines are preserved (vendor code and purchase price are carried
over); only missing supplier lines are added and the preferred flag is
re-pointed when a higher-ranked supplier is present. Diff-aware; honors
``SYNC_DRY_RUN``; ``UPDATE_MAX_ITEMS`` caps writes.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

RANKING = [512, 264, 510, 576]  # SanMar > Momentec(Augusta) > S&S > UA
KEY_FIELDS = {  # supplier vendor id -> item field carrying its code
    512: "custitem_sanmar_style",
    264: "custitem_mtec_item_sku",
    510: "custitem_ss_sku",
}


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    fields = ", ".join(KEY_FIELDS.values())
    where = " OR ".join(f"{f} IS NOT NULL" for f in KEY_FIELDS.values())
    items = client.suiteql(f"SELECT id, {fields} FROM item WHERE {where}")
    print(f"supplier-matched items: {len(items):,}")

    ids = [str(r["id"]) for r in items]
    by_id = {str(r["id"]): r for r in items}
    existing: dict[str, dict[int, dict]] = {}
    for i in range(0, len(ids), 300):
        chunk = ids[i : i + 300]
        in_list = ", ".join(f"'{_sql_escape(x)}'" for x in chunk)
        for r in client.suiteql(
            "SELECT item, vendor, vendorcode, preferredvendor, purchaseprice "
            f"FROM itemvendor WHERE item IN ({in_list})"
        ):
            existing.setdefault(str(r["item"]), {})[int(r["vendor"])] = r

    considered = written = unchanged = failures = 0
    sample_shown = 0
    for rid in ids:
        row = by_id[rid]
        cur = existing.get(rid, {})
        matched_vids = [
            vid for vid, f in KEY_FIELDS.items() if str(row.get(f) or "").strip()
        ]
        if not matched_vids:
            continue
        all_vids = list(dict.fromkeys(list(cur.keys()) + matched_vids))
        preferred = next((v for v in RANKING if v in all_vids), all_vids[0])

        need_change = any(vid not in cur for vid in matched_vids)
        for vid, line in cur.items():
            is_pref = str(line.get("preferredvendor") or "").upper() in ("T", "TRUE", "1")
            if is_pref != (vid == preferred):
                need_change = True
        if not need_change:
            unchanged += 1
            continue
        if max_items and considered >= max_items:
            continue
        considered += 1

        lines = []
        for vid in all_vids:
            line: dict[str, object] = {
                "vendor": {"id": str(vid)},
                "preferredVendor": vid == preferred,
            }
            old = cur.get(vid)
            code = (old or {}).get("vendorcode") or str(row.get(KEY_FIELDS.get(vid, "")) or "")
            if str(code).strip():
                line["vendorCode"] = str(code)
            price = (old or {}).get("purchaseprice")
            if price is not None and str(price).strip():
                try:
                    line["purchasePrice"] = float(price)
                except ValueError:
                    pass
            lines.append(line)

        if sample_shown < 5:
            sample_shown += 1
            print(f"  plan item {rid}: preferred={preferred}, lines="
                  f"{[(str(x['vendor']['id']), x['preferredVendor']) for x in lines]}")
        if not allow_write:
            written += 1
            continue
        try:
            client.update_record("inventoryItem", rid, {"itemVendor": {"items": lines}})
            written += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if failures <= 10:
                print(f"  FAILED item {rid}: {str(exc)[:150]}")

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\nvendor sublist: {verb} {written} item(s); unchanged: {unchanged}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
