"""Seed a Preferred Vendor on matrix PARENTS that have none -- from their kids.

Andy, 2026-08-12: "all items would need a preferred vendor". Children get one
at creation (the RESTlet seeds the vendor sublist), but every parent created
before today went out bare -- the ~900 SanMar ramp parents and the S&S pilot
ones. Both create paths now seed the parent too; this heals the ones already
in NetSuite.

The vendor a parent gets is the one its CHILDREN agree on (majority of the
children's preferred-vendor lines) -- the parent has no vendor key fields of
its own, so the children are the only trustworthy signal. A parent whose
children carry no vendor lines, or that already has any vendor line, is left
alone. Honours ``SYNC_DRY_RUN``.
"""

from __future__ import annotations

from collections import Counter

from run_status import exit_code

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

CHUNK = 250


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    client = NetSuiteClient(cfg.netsuite)

    parents = client.suiteql(
        "SELECT id, itemid FROM item WHERE matrixtype = 'PARENT' "
        "AND isinactive = 'F'"
    )
    ids = [str(p["id"]) for p in parents]
    name_of = {str(p["id"]): str(p.get("itemid") or "") for p in parents}
    print(f"{len(ids)} active matrix parent(s)")

    have_vendor: set[str] = set()
    child_pref: dict[str, Counter] = {}
    for i in range(0, len(ids), CHUNK):
        part = ids[i:i + CHUNK]
        in_list = ", ".join(part)
        try:
            for r in client.suiteql(
                f"SELECT item FROM itemvendor WHERE item IN ({in_list})"
            ):
                have_vendor.add(str(r["item"]))
            # The children's preferred vendors, tallied per parent.
            for r in client.suiteql(
                "SELECT i.parent AS p, iv.vendor AS v "
                "FROM item i, itemvendor iv WHERE iv.item = i.id "
                f"AND iv.preferredvendor = 'T' AND i.parent IN ({in_list})"
            ):
                child_pref.setdefault(str(r["p"]), Counter())[str(r["v"])] += 1
        except Exception as exc:  # noqa: BLE001 - a throttled chunk skips
            print(f"  lookup failed for a chunk of {len(part)}: {str(exc)[:90]}")

    todo = [(pid, child_pref[pid].most_common(1)[0][0])
            for pid in ids
            if pid not in have_vendor and pid in child_pref]
    print(f"{len(have_vendor)} parent(s) already carry a vendor line; "
          f"{len(todo)} bare parent(s) have children with a preferred vendor")

    written = failures = 0
    fail_429 = 0
    for pid, vid in todo:
        body = {"itemVendor": {"items": [{
            "vendor": {"id": vid},
            "preferredVendor": True,
            "vendorCode": name_of.get(pid, ""),
        }]}}
        if not allow_write:
            written += 1
            if written <= 10:
                print(f"  WOULD seed parent {name_of.get(pid)} ({pid}) "
                      f"with vendor {vid}")
            continue
        try:
            client.update_record("inventoryItem", pid, body)
            written += 1
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if "429" in str(exc):
                fail_429 += 1
            if failures <= 10:
                print(f"  FAILED parent {name_of.get(pid)} ({pid}): "
                      f"{str(exc)[:120]}")

    verb = "seeded" if allow_write else "WOULD seed (dry run)"
    print(f"\nparent vendor backfill: {verb} {written} parent(s); "
          f"failures: {failures}")
    return exit_code("parent vendor backfill", failures,
                     failures - fail_429, written + failures)


if __name__ == "__main__":
    raise SystemExit(main())
