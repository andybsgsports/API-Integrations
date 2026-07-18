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

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape

PAIRS = [
    ("TSK11-Crd-Large", "TSK11-Cardinal-Large"),
    ("TSK11-KG-Large", "TSK11-Kelly Green-Large"),
    ("TSK11-Pi-Large", "TSK11-Pink-Large"),
]

# The first inventory column the account's SuiteQL accepts wins.
QTY_COLUMNS = ("totalquantityonhand", "quantityonhand", "quantityavailable")


def fetch(client: NetSuiteClient, names: list[str]) -> tuple[dict[str, dict], str]:
    in_list = ", ".join(f"'{_sql_escape(n)}'" for n in names)
    last_err = ""
    for qty_col in QTY_COLUMNS:
        try:
            rows = client.suiteql(
                f"SELECT id, itemid, isinactive, upccode, {qty_col} AS qty "
                f"FROM item WHERE itemid IN ({in_list})"
            )
            return {str(r["itemid"]): r for r in rows}, qty_col
        except Exception as exc:  # noqa: BLE001 - unknown column, try the next
            last_err = str(exc)[:80]
    raise RuntimeError(f"no usable inventory column ({last_err})")


def qty_of(row: dict | None) -> float:
    try:
        return float(row.get("qty") or 0) if row else 0.0
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    client = NetSuiteClient(cfg.netsuite)

    all_names = [n for pair in PAIRS for n in pair]
    by_name, qty_col = fetch(client, all_names)
    print(f"inventory column: {qty_col}; records found: {len(by_name)}/6")

    flagged = failures = written = 0
    for abbrev, canonical in PAIRS:
        a, c = by_name.get(abbrev), by_name.get(canonical)
        print(f"\n=== {abbrev!r} vs {canonical!r}")
        for label, r in (("abbrev", a), ("canonical", c)):
            if r is None:
                print(f"  {label:<10} MISSING")
            else:
                print(f"  {label:<10} id {r['id']}  qty {qty_of(r):g}  "
                      f"inactive {r.get('isinactive')}  upc {r.get('upccode') or '-'}")
        if a is None and c is None:
            print("  -> neither record found; nothing to do")
            continue
        if a is None:
            print("  -> abbreviated record already gone; nothing to do")
            continue
        if c is None:
            # target name is free after all -- just rename the abbreviated one
            keeper, loser = a, None
        else:
            qa, qc = qty_of(a), qty_of(c)
            if (qa > 0) == (qc > 0):
                flagged += 1
                print(f"  -> FLAGGED: can't decide by inventory (abbrev {qa:g}, "
                      f"canonical {qc:g}) -- needs a human")
                continue
            keeper, loser = (a, c) if qa > 0 else (c, a)
        plan = []
        if loser is not None:
            plan.append((str(loser["id"]),
                         {"itemId": f"{loser['itemid']}-DUP", "isInactive": True},
                         f"retire {loser['itemid']!r} -> '{loser['itemid']}-DUP' (inactive)"))
        if str(keeper["itemid"]) == abbrev:
            plan.append((str(keeper["id"]), {"itemId": canonical},
                         f"rename keeper {abbrev!r} -> {canonical!r}"))
        else:
            print(f"  keeper already holds the canonical name (id {keeper['id']})")
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
                print(f"  FAILED: {str(exc)[:100]} :: {str(detail)[:200]}")

    verb = "applied" if allow_write else "WOULD apply (dry run)"
    print(f"\ntsk11 dup fix: {verb} {written} update(s); flagged for human: {flagged}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
