"""Preview the S&S Activewear items NetSuite is missing -- read-only.

The S&S discover phase: diff today's downloaded catalog (195k SKUs) against
the live NetSuite catalog and bucket every missing SKU, the same shape as
``sanmar_create_preview.py``. Nothing here writes to NetSuite.

The buckets differ from SanMar's in one important way: S&S styles routinely
share a NetSuite parent with SanMar (both carry the same blank goods, and
``ss_backfill`` already joins ``style_name`` -> ``vendorname``), so an
"existing parent" here usually means ADOPTING a parent another vendor
created -- the S&S children slot into its colour/size grid. The buckets:

* **already in NetSuite** -- the SKU's GTIN matches an item's ``upcCode``, or
  its (colour, size) combo already exists under the adopted parent.
* **new children under an existing parent** -- style has a matrix parent,
  this colour/size doesn't exist yet.
* **children of a net-new parent style** -- no matrix parent with that
  ``itemid`` anywhere; the parent must be created first.
* **name collisions** -- an item with the style's ``itemid`` exists but is
  NOT a matrix parent. Creation for these is blocked pending review: writing
  a parent would collide with (or corrupt) whatever that item is.

Existing-child detection compares NORMALISED option names, not raw feed
strings (``normalize_option_name`` + ``normalize_size``), so ``'J. Navy'``
vs ``'J.Navy'`` can't make a duplicate child look "new" -- the same identity
rule the whole pipeline uses. Existing children are found with ONE batched
query per 250 parents, not SanMar's one-query-per-parent shape, which does
not survive this feed's style count.

Emits: ``data/ss_new_parents.txt``, ``data/ss_new_children.csv``,
``data/ss_new_colors.txt``, ``data/ss_new_sizes.txt``.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import (
    COLOR_LIST,
    SIZE_LIST,
    normalize_option_name,
)
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.sizes import normalize_size
from ss_activewear_netsuite.config import get_config as ss_config

ROOT = Path(__file__).resolve().parents[1]

GTIN_CHUNK = 300
STYLE_CHUNK = 250
PARENT_CHUNK = 250


def load_products() -> list[dict]:
    products_file = Path(ss_config().download_dir) / "products.json"
    if not products_file.exists():
        raise SystemExit(
            f"{products_file} missing -- run `ss-sync download` first "
            "(the ss pipeline job's download step does this)"
        )
    return json.loads(products_file.read_text(encoding="utf-8"))


def _chunked_suiteql(client: NetSuiteClient, sql_for: Callable[[list[str]], str],
                     values: list[str], chunk: int) -> list[dict]:
    """Run a chunked IN-list query; a failed chunk falls back to sub-chunks.

    A 429 that outlasts the client's retry budget must cost that chunk's
    matches, not the run (the lesson of S&S run 30582440348).
    """
    rows: list[dict] = []
    for i in range(0, len(values), chunk):
        part = values[i:i + chunk]
        try:
            rows.extend(client.suiteql(sql_for(part)))
            continue
        except Exception:  # noqa: BLE001 - retry smaller before giving up
            pass
        for j in range(0, len(part), 50):
            sub = part[j:j + 50]
            try:
                rows.extend(client.suiteql(sql_for(sub)))
            except Exception as exc:  # noqa: BLE001
                print(f"  lookup failed for a batch of {len(sub)}: {str(exc)[:100]}")
    return rows


def _norm_names_by_id(client: NetSuiteClient, list_type: str) -> dict[str, str]:
    """list value internal id -> normalised name, for combo comparison."""
    rows = client.suiteql(f"SELECT id, name FROM {list_type}")
    return {
        str(r["id"]): normalize_option_name(str(r.get("name") or ""))
        for r in rows
    }


#: Spelled-out X-family sizes folded onto the canonical ``nX-`` spelling, for
#: COMPARISON ONLY. ``normalize_size`` maps the feeds' ``2XL``/``XXL`` to
#: ``2X-Large`` but leaves a spelled-out ``XX-Large`` alone, so a child stored
#: under the older spelling would not collide with the feed's -- and this
#: preview would call an existing child "new", i.e. queue a duplicate create.
#: Deliberately NOT folded inside ``normalize_size`` itself: that function's
#: output is matched against live list-value names by exact string
#: (``OptionMaps.size_candidates``), so changing it would alter matching for
#: every vendor's update path to fix a problem confined to this diff. The
#: global question -- whether the size list carries both spellings, like the
#: colour list carried Forest/Forrest -- wants an audit first.
_SIZE_FOLD = re.compile(r"^(x+)(large|small)$")


def fold_size(spelled: str) -> str:
    """``xxlarge`` -> ``2xlarge``; anything else unchanged (already normalised)."""
    m = _SIZE_FOLD.match(spelled)
    if not m:
        return spelled
    n = len(m.group(1))
    return spelled if n == 1 else f"{n}x{m.group(2)}"


def combo_key(color_name: str, size_name: str) -> tuple[str, str]:
    return (
        normalize_option_name(color_name),
        fold_size(normalize_option_name(normalize_size(size_name))),
    )


def _brand_key(name: str) -> str:
    return normalize_option_name(name)


def brand_allowlist() -> set[str]:
    """Brands creation is scoped to, from ``SS_CREATE_BRANDS`` (empty = all).

    Andy, 2026-08-10: bring in specific brands rather than all 5,027 net-new
    S&S styles (~3x the current SanMar catalogue). Names are compared with
    the same punctuation/case-insensitive rule as option values, so
    'Bella+Canvas' and 'BELLA + CANVAS' are one brand.
    """
    raw = os.environ.get("SS_CREATE_BRANDS", "")
    return {_brand_key(b) for b in raw.split(",") if b.strip()}


_ALLOWED = brand_allowlist()


def brand_allowed(brand: str) -> bool:
    return not _ALLOWED or _brand_key(brand) in _ALLOWED


def main() -> int:
    products = load_products()
    styles: dict[str, list[dict]] = {}
    no_gtin = 0
    for p in products:
        style = str(p.get("style_name") or "").strip()
        if not style:
            continue
        styles.setdefault(style, []).append(p)
        if not str(p.get("gtin") or "").strip():
            no_gtin += 1
    total = sum(len(v) for v in styles.values())
    print(f"S&S catalog: {len(styles)} styles, {total} SKUs "
          f"({no_gtin} without a GTIN)")

    client = NetSuiteClient(ns_config().netsuite)

    # -- pass 1: which GTINs are already on an item's upcCode? ---------------
    gtins = sorted({str(p.get("gtin") or "").strip()
                    for v in styles.values() for p in v
                    if str(p.get("gtin") or "").strip()})
    rows = _chunked_suiteql(
        client,
        lambda part: ("SELECT upccode FROM item WHERE upccode IN ("
                      + ", ".join(f"'{_sql_escape(g)}'" for g in part) + ")"),
        gtins, GTIN_CHUNK,
    )
    known_gtins = {str(r["upccode"]) for r in rows}
    print(f"GTIN match: {len(known_gtins)} of {len(gtins)} distinct GTINs "
          f"already on a NetSuite upcCode")

    # -- pass 2: which styles have a matrix parent (or a colliding item)? ----
    style_names = sorted(styles)
    rows = _chunked_suiteql(
        client,
        lambda part: ("SELECT id, itemid, matrixtype FROM item "
                      "WHERE LOWER(itemid) IN ("
                      + ", ".join(f"LOWER('{_sql_escape(s)}')" for s in part) + ")"),
        style_names, STYLE_CHUNK,
    )
    by_lower = {s.lower(): s for s in style_names}
    parent_refs: dict[str, str] = {}
    collisions: dict[str, str] = {}
    for r in rows:
        style = by_lower.get(str(r.get("itemid") or "").strip().lower())
        if style is None:
            continue
        if str(r.get("matrixtype") or "").upper() == "PARENT":
            parent_refs.setdefault(style, str(r["id"]))
        else:
            collisions.setdefault(style, str(r["id"]))
    collisions = {s: i for s, i in collisions.items() if s not in parent_refs}
    print(f"parents: {len(parent_refs)} of {len(style_names)} styles already "
          f"have a matrix parent to adopt; {len(collisions)} name collision(s) "
          f"with non-matrix items")

    # -- pass 3: existing (colour, size) combos under the adopted parents ----
    color_norm = _norm_names_by_id(client, COLOR_LIST)
    size_norm = _norm_names_by_id(client, SIZE_LIST)
    parent_ids = sorted(parent_refs.values())
    rows = _chunked_suiteql(
        client,
        lambda part: ("SELECT parent, custitem_bsg_color AS c, "
                      "custitem_bsg_size AS sz FROM item WHERE parent IN ("
                      + ", ".join(part) + ")"),
        parent_ids, PARENT_CHUNK,
    )
    combos: dict[str, set[tuple[str, str]]] = {}
    for r in rows:
        key = (color_norm.get(str(r.get("c") or ""), ""),
               size_norm.get(str(r.get("sz") or ""), ""))
        if key[0] and key[1]:
            combos.setdefault(str(r["parent"]), set()).add(key)

    # -- bucket every SKU ----------------------------------------------------
    exists_gtin = exists_combo = new_children = 0
    new_parent_rows = blocked_rows = filtered_rows = 0
    new_parent_styles: set[str] = set()
    child_styles: Counter[str] = Counter()
    used_colors: set[str] = set()
    used_sizes: set[str] = set()
    child_rows: list[str] = []
    # Per-brand tallies. `carried` -- SKUs of that brand already in NetSuite --
    # is the demand signal: a brand we already stock deep is one we sell, and
    # that is what picks the creation allowlist (Andy, 2026-08-10).
    carried: Counter[str] = Counter()
    brand_new_skus: Counter[str] = Counter()
    brand_new_styles: dict[str, set[str]] = {}

    for style, skus in styles.items():
        pid = parent_refs.get(style)
        have = combos.get(pid or "", set())
        for p in skus:
            brand = str(p.get("brand_name") or "").strip() or "(no brand)"
            gtin = str(p.get("gtin") or "").strip()
            if gtin and gtin in known_gtins:
                exists_gtin += 1
                carried[brand] += 1
                continue
            color = str(p.get("color_name") or "").strip()
            size = str(p.get("size_name") or "").strip()
            if pid is not None:
                if combo_key(color, size) in have:
                    exists_combo += 1
                    carried[brand] += 1
                    continue
                if not brand_allowed(brand):
                    filtered_rows += 1
                    continue
                new_children += 1
                child_styles[style] += 1
                brand_new_skus[brand] += 1
                used_colors.add(color)
                used_sizes.add(normalize_size(size))
                child_rows.append(
                    f"{style},{p.get('brand_name') or ''},{color},{size},"
                    f"{p.get('sku') or ''},{gtin}"
                )
            elif style in collisions:
                blocked_rows += 1
            elif not brand_allowed(brand):
                filtered_rows += 1
                brand_new_styles.setdefault(brand, set()).add(style)
                brand_new_skus[brand] += 1
            else:
                new_parent_rows += 1
                new_parent_styles.add(style)
                brand_new_styles.setdefault(brand, set()).add(style)
                brand_new_skus[brand] += 1
                used_colors.add(color)
                used_sizes.add(normalize_size(size))

    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    parents_sorted = sorted(new_parent_styles)
    (data / "ss_new_parents.txt").write_text(
        "\n".join(parents_sorted) + ("\n" if parents_sorted else ""), encoding="utf-8")
    (data / "ss_new_children.csv").write_text(
        "style,brand,color,size,sku,gtin\n" + "".join(r + "\n" for r in child_rows),
        encoding="utf-8")
    (data / "ss_new_colors.txt").write_text(
        "\n".join(sorted(used_colors)) + ("\n" if used_colors else ""), encoding="utf-8")
    (data / "ss_new_sizes.txt").write_text(
        "\n".join(sorted(s for s in used_sizes if s))
        + ("\n" if used_sizes else ""), encoding="utf-8")

    print("")
    print(f"already in NetSuite (GTIN)          : {exists_gtin:>7}")
    print(f"already in NetSuite (colour/size)   : {exists_combo:>7}  "
          f"(combo exists under the adopted parent; item lacks our GTIN)")
    print(f"NEW children under existing parents : {new_children:>7}  "
          f"across {len(child_styles)} style(s) -> data/ss_new_children.csv")
    for style, n in child_styles.most_common(10):
        print(f"    {style}: {n}")
    print(f"NEW rows under net-new parent styles: {new_parent_rows:>7}  "
          f"across {len(parents_sorted)} style(s) -> data/ss_new_parents.txt")
    if parents_sorted:
        sample = ", ".join(parents_sorted[:20])
        more = "" if len(parents_sorted) <= 20 else f" (+{len(parents_sorted) - 20} more)"
        print(f"    {sample}{more}")
    print(f"BLOCKED by itemid collisions        : {blocked_rows:>7}  "
          f"across {len(collisions)} style(s)"
          + (f" ({', '.join(sorted(collisions)[:10])}"
             + ("..." if len(collisions) > 10 else "") + ")" if collisions else ""))
    if _ALLOWED:
        print(f"FILTERED OUT (brand not on list)    : {filtered_rows:>7}  "
              f"(SS_CREATE_BRANDS scopes creation)")
    print(f"missing rows reference {len(used_colors)} distinct colour(s), "
          f"{len([s for s in used_sizes if s])} size(s) "
          f"-> ss_new_colors.txt / ss_new_sizes.txt (ensure-values input)")

    # -- brand breakdown -----------------------------------------------------
    # Sorted by how deep we ALREADY stock the brand: that is the demand
    # signal, and it is what picks the allowlist. A brand with thousands of
    # carried SKUs is one BSG sells; a brand with zero carried and thousands
    # available is catalogue we have never chosen to stock.
    brands = sorted(
        set(carried) | set(brand_new_skus),
        key=lambda b: (-carried[b], -brand_new_skus[b], b),
    )
    summary = data / "ss_brand_summary.csv"
    with summary.open("w", encoding="utf-8", newline="") as fh:
        fh.write("brand,carried_skus,new_skus,new_styles\n")
        for b in brands:
            fh.write(f"\"{b}\",{carried[b]},{brand_new_skus[b]},"
                     f"{len(brand_new_styles.get(b, ()))}\n")
    print(f"\nBRAND BREAKDOWN ({len(brands)} brands) -> {summary.relative_to(ROOT)}")
    print(f"  {'brand':<32} {'carried':>8} {'new SKUs':>9} {'new styles':>11}")
    for b in brands[:25]:
        print(f"  {b[:32]:<32} {carried[b]:>8} {brand_new_skus[b]:>9} "
              f"{len(brand_new_styles.get(b, ())):>11}")
    stocked = [b for b in brands if carried[b]]
    print(f"  ({len(stocked)} brand(s) already stocked; "
          f"{len(brands) - len(stocked)} carried zero SKUs today)")
    print("\nread-only preview: nothing was written to NetSuite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
