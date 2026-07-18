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


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    client = NetSuiteClient(cfg.netsuite)

    all_names = [n for pair in PAIRS for n in pair]
    rows, qty_col = fetch(client, all_names)
    print(f"inventory column: {qty_col}; records found by suffix search: {len(rows)}")
    for r in rows:
        print(f"  id {r['id']:>7}  qty {qty_of(r):>4g}  inactive {r.get('isinactive')}  "
              f"{str(r.get('itemid'))!r}")

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
        keeper_plain = str(keeper["itemid"]).split(" : ")[-1].strip()
        if keeper_plain.upper() == abbrev.upper():
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
