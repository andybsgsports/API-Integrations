"""Resolve the 3 TSK11 duplicate-name pairs left over from the color renames.

The color rename run couldn't rename ``TSK11-Crd/KG/Pi-Large`` because items
already exist under the canonical target names (Cardinal / Kelly Green /
Pink -- token meanings confirmed by the user). Per the user's rule, the
record that carries inventory is the keeper:

* loser: renamed to ``<itemid>-DUP`` (frees the canonical name -- NetSuite
  enforces itemid uniqueness even across inactive records) and inactivated.
* keeper: renamed to the canonical name if it still has the abbreviated one.
* both-with-inventory or neither-with-inventory: skipped and flagged for a
  human -- the rule can't break the tie.

Honors ``SYNC_DRY_RUN``.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, COLOR_LIST, SIZE_FIELD, SIZE_LIST
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

PAIRS = [
    ("TSK11-Crd-Large", "TSK11-Cardinal-Large"),
    ("TSK11-KG-Large", "TSK11-Kelly Green-Large"),
    ("TSK11-Pi-Large", "TSK11-Pink-Large"),
]

# The first inventory column the account's SuiteQL accepts wins.
QTY_COLUMNS = ("totalquantityonhand", "quantityonhand", "quantityavailable")


def fetch(client: NetSuiteClient, names: list[str]) -> tuple[list[dict], str]:
    """All records whose itemid equals OR ends with any of the names.

    The live rename run proved exact itemid matching misses the blockers:
    NetSuite rejected every rename as a duplicate name while
    ``itemid = 'TSK11-Cardinal-Large'`` found nothing -- matrix children
    store parent-prefixed itemids ('TSK11 : TSK11-Cardinal-Large') but the
    uniqueness check compares the child segment. A suffix LIKE exposes them.
    """
    conds = " OR ".join(
        f"UPPER(itemid) LIKE UPPER('%{_sql_escape(n)}')" for n in names
    )
    last_err = ""
    for qty_col in QTY_COLUMNS:
        try:
            rows = client.suiteql(
                f"SELECT id, itemid, isinactive, upccode, {qty_col} AS qty "
                f"FROM item WHERE {conds}"
            )
            return rows, qty_col
        except Exception as exc:  # noqa: BLE001 - unknown column, try the next
            last_err = str(exc)[:80]
    raise RuntimeError(f"no usable inventory column ({last_err})")


def qty_of(row: dict | None) -> float:
    try:
        return float(row.get("qty") or 0) if row else 0.0
    except (TypeError, ValueError):
        return 0.0


def account_diff(client: NetSuiteClient) -> None:
    """Full-record GET of one broken item vs one healthy sibling.

    Every PATCH on the 3 items fails record validation with 'You may not use
    duplicate accounts on an item' -- an accounting misconfiguration on the
    records themselves. Diff the account-ish fields against a sibling that
    saves fine (94020, TSK11-Black-Large) to find the duplicate.
    """
    records = {}
    for label, rid in (("BROKEN 97565 (Crd)", "97565"), ("HEALTHY 94020 (Black)", "94020")):
        try:
            records[label] = client.get_record("inventoryItem", rid)
        except Exception as exc:  # noqa: BLE001
            print(f"  GET {rid} failed: {str(exc)[:120]}")
            return

    def summarize(rec: dict) -> dict[str, str]:
        out = {}
        for k, v in sorted(rec.items()):
            if "account" not in k.lower():
                continue
            if isinstance(v, dict):
                out[k] = f"{v.get('id')}:{v.get('refName', '')}"
            else:
                out[k] = str(v)
        return out

    sums = {label: summarize(rec) for label, rec in records.items()}
    keys = sorted(set().union(*[s.keys() for s in sums.values()]))
    print("\n=== account-field diff (broken vs healthy) ===")
    for k in keys:
        vals = [sums[label].get(k, "<absent>") for label in sums]
        marker = "  <<< DIFFERS" if len(set(vals)) > 1 else ""
        print(f"  {k:<32} broken={vals[0]:<40} healthy={vals[1]}{marker}")
    b_keys, h_keys = (set(records[label].keys()) for label in records)
    only_b, only_h = sorted(b_keys - h_keys), sorted(h_keys - b_keys)
    if only_b:
        print(f"  top-level keys only on broken: {only_b}")
    if only_h:
        print(f"  top-level keys only on healthy: {only_h}")


# Filled at run time from the healthy sibling (TSK11-Black-Large, id 94020):
# the asset account every non-broken TSK11 child uses.
HEALTHY_ASSET: dict[str, str] = {"id": ""}


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    client = NetSuiteClient(cfg.netsuite)
    if (os.environ.get("TSK11_DIFF") or "").lower() == "true":
        account_diff(client)
    try:
        healthy = client.get_record("inventoryItem", "94020")
        HEALTHY_ASSET["id"] = str((healthy.get("assetAccount") or {}).get("id") or "")
        print(f"healthy sibling assetAccount: {HEALTHY_ASSET['id']}")
    except Exception as exc:  # noqa: BLE001
        print(f"healthy sibling GET failed ({str(exc)[:80]}); account repair disabled")

    all_names = [n for pair in PAIRS for n in pair]
    rows, qty_col = fetch(client, all_names)
    print(f"inventory column: {qty_col}; records found by suffix search: {len(rows)}")
    for r in rows:
        print(f"  id {r['id']:>7}  qty {qty_of(r):>4g}  inactive {r.get('isinactive')}  "
              f"{str(r.get('itemid'))!r}")

    # The live rename failed with a 'duplicate' rejection even though nothing
    # holds the target names -- suspect a duplicate matrix option COMBINATION
    # under the same parent (consolidation repointed color values). Dump every
    # sibling with its color/size option so the collision is visible.
    ids = [str(r["id"]) for r in rows]
    if ids:
        parent_of = {
            str(r["id"]): str(r.get("parent") or "")
            for r in client.suiteql(
                f"SELECT id, parent FROM item WHERE id IN ({', '.join(ids)})"
            )
        }
        parent_ids = sorted({p for p in parent_of.values() if p})
        print(f"\nparents of the 3 items: {parent_of}")
        if parent_ids:
            sibs = client.suiteql(
                f"SELECT id, itemid, isinactive, {qty_col} AS qty, "
                f"{COLOR_FIELD} AS c, {SIZE_FIELD} AS s "
                f"FROM item WHERE parent IN ({', '.join(parent_ids)})"
            )
            opt_ids = {str(r.get("c")) for r in sibs if r.get("c")}
            size_ids = {str(r.get("s")) for r in sibs if r.get("s")}
            color_name = {
                str(r["id"]): str(r.get("name") or "")
                for r in client.suiteql(
                    f"SELECT id, name FROM {COLOR_LIST} "
                    f"WHERE id IN ({', '.join(sorted(opt_ids)) or '0'})"
                )
            }
            size_name = {
                str(r["id"]): str(r.get("name") or "")
                for r in client.suiteql(
                    f"SELECT id, name FROM {SIZE_LIST} "
                    f"WHERE id IN ({', '.join(sorted(size_ids)) or '0'})"
                )
            }
            print(f"siblings under parent(s) {parent_ids}: {len(sibs)}")
            for r in sorted(sibs, key=lambda r: str(r.get("itemid"))):
                c = color_name.get(str(r.get("c")), str(r.get("c") or "-"))
                s = size_name.get(str(r.get("s")), str(r.get("s") or "-"))
                print(f"  id {r['id']:>7}  qty {qty_of(r):>4g}  "
                      f"inactive {r.get('isinactive')}  color {c!r:<22} "
                      f"size {s!r:<12} {str(r.get('itemid'))!r}")

    def endswith(r: dict, name: str) -> bool:
        return str(r.get("itemid") or "").strip().upper().endswith(name.upper())

    flagged = failures = written = 0
    for abbrev, canonical in PAIRS:
        holders_a = [r for r in rows if endswith(r, abbrev)]
        holders_c = [r for r in rows if endswith(r, canonical)]
        print(f"\n=== {abbrev!r} vs {canonical!r}")
        if len(holders_a) != 1 or len(holders_c) > 1:
            flagged += 1
            print(f"  -> FLAGGED: unexpected record counts (abbrev x{len(holders_a)}, "
                  f"canonical x{len(holders_c)}) -- needs a human")
            continue
        a = holders_a[0]
        c = holders_c[0] if holders_c else None
        if c is None:
            # target name genuinely free -- just rename the abbreviated one
            keeper, loser = a, None
        else:
            qa, qc = qty_of(a), qty_of(c)
            if (qa > 0) == (qc > 0):
                flagged += 1
                print(f"  -> FLAGGED: can't decide by inventory (abbrev {qa:g}, "
                      f"canonical {qc:g}) -- needs a human")
                continue
            keeper, loser = (a, c) if qa > 0 else (c, a)
        # matrix children come back parent-prefixed ('TSK11 : <name>'); the
        # writable itemId is only the child segment.
        plan = []
        if loser is not None:
            loser_plain = str(loser["itemid"]).split(" : ")[-1].strip()
            plan.append((str(loser["id"]),
                         {"itemId": f"{loser_plain}-DUP", "isInactive": True},
                         f"retire {loser['itemid']!r} -> '{loser_plain}-DUP' (inactive)"))
        # These records fail EVERY save with 'You may not use duplicate
        # accounts on an item': their assetAccount equals their cogsAccount
        # (128/128) while every healthy sibling is 127/128. Repair the asset
        # account (to the healthy sibling's) in the same PATCH as the rename,
        # or nothing can ever be written to them again.
        body: dict = {}
        desc_bits: list[str] = []
        try:
            rec = client.get_record("inventoryItem", str(keeper["id"]))
            asset = str((rec.get("assetAccount") or {}).get("id") or "")
            cogs = str((rec.get("cogsAccount") or {}).get("id") or "")
            if asset and asset == cogs and HEALTHY_ASSET["id"]:
                body["assetAccount"] = {"id": HEALTHY_ASSET["id"]}
                desc_bits.append(
                    f"fix assetAccount {asset} -> {HEALTHY_ASSET['id']} "
                    f"(was duplicated with cogsAccount)")
        except Exception as exc:  # noqa: BLE001
            print(f"  keeper record GET failed ({str(exc)[:80]}); "
                  "skipping account repair")
        keeper_plain = str(keeper["itemid"]).split(" : ")[-1].strip()
        if keeper_plain.upper() == abbrev.upper():
            body["itemId"] = canonical
            desc_bits.append(f"rename {abbrev!r} -> {canonical!r}")
        else:
            print(f"  keeper already holds the canonical name (id {keeper['id']})")
        if body:
            plan.append((str(keeper["id"]), body, "; ".join(desc_bits)))
        for rid, body, desc in plan:
            print(f"  {'would ' if not allow_write else ''}{desc}")
            if not allow_write:
                written += 1
                continue
            try:
                client.update_record("inventoryItem", rid, body)
                written += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                detail = getattr(exc, "payload", "")
                print(f"  FAILED: {str(exc)[:120]} :: {str(detail)[:1200]}")

    verb = "applied" if allow_write else "WOULD apply (dry run)"
    print(f"\ntsk11 dup fix: {verb} {written} update(s); flagged for human: {flagged}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
