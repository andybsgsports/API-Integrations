"""Can an S&S child adopt an existing parent whose grid lacks its colour?

The S&S create phase adopts parents another vendor already built: the matrix
RESTlet resolves a parent by NAME, so a child posted for style '3001C' lands
under whatever parent carries that itemid -- 395 of today's 5,463 S&S styles
(preview run 31427513256). The open question is what a matrix PARENT does
with an option value it does not already carry: if the parent's grid is a
restricted multi-select, a child in a new colour is rejected until the parent
is extended, and creation must extend it first.

That question decides the shape of the create path, and guessing it is how
322 children died on 2026-08-08 (a retired colour id NetSuite rejected
outright). So this probe READS ONLY, and reports:

* how NetSuite represents a parent's grid in SuiteQL (the raw value -- the
  representation is discovered here, not assumed);
* for a sample of adoptable styles, which S&S colours/sizes the parent's grid
  already covers and which it does not;
* how many children those parents already have, so an "empty grid" reading
  can be told apart from "no children yet".

Env: ``SS_PROBE_STYLES`` (how many adoptable styles to sample, default 8).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.sizes import normalize_size
from ss_activewear_netsuite.config import get_config as ss_config

COLOR_FIELD = "custitem_bsg_color"
SIZE_FIELD = "custitem_bsg_size"


def main() -> int:
    sample_size = int(os.environ.get("SS_PROBE_STYLES", "8") or "8")
    products_file = Path(ss_config().download_dir) / "products.json"
    if not products_file.exists():
        print(f"{products_file} missing -- run `ss-sync download` first")
        return 1
    products = json.loads(products_file.read_text(encoding="utf-8"))

    by_style: dict[str, list[dict]] = {}
    for p in products:
        style = str(p.get("style_name") or "").strip()
        if style:
            by_style.setdefault(style, []).append(p)

    client = NetSuiteClient(ns_config().netsuite)

    # Adoptable styles: an S&S style whose itemid is already a matrix parent.
    names = sorted(by_style)
    parents: list[dict] = []
    for i in range(0, len(names), 250):
        part = names[i:i + 250]
        in_list = ", ".join(f"LOWER('{_sql_escape(s)}')" for s in part)
        try:
            parents.extend(client.suiteql(
                "SELECT id, itemid FROM item WHERE matrixtype = 'PARENT' "
                f"AND LOWER(itemid) IN ({in_list})"
            ))
        except Exception as exc:  # noqa: BLE001 - a throttled chunk costs its styles
            print(f"  parent lookup failed for {len(part)} style(s): {str(exc)[:90]}")
        if len(parents) >= sample_size * 4:
            break
    if not parents:
        print("no adoptable parents found -- nothing to probe")
        return 0
    print(f"{len(parents)} adoptable parent(s) found in this slice; "
          f"probing {min(sample_size, len(parents))}\n")

    # What does a parent's grid even look like in SuiteQL? Print the raw value
    # for the first parent before interpreting anything.
    first = parents[0]
    try:
        raw = client.suiteql(
            f"SELECT id, itemid, {COLOR_FIELD} AS c, {SIZE_FIELD} AS sz "
            f"FROM item WHERE id = {int(first['id'])}"
        )
        print(f"RAW parent row for {first['itemid']}: {raw}")
    except Exception as exc:  # noqa: BLE001
        print(f"RAW parent read failed: {str(exc)[:200]}")
    print("")

    covered = Counter()
    for parent in parents[:sample_size]:
        style = str(parent["itemid"])
        pid = int(parent["id"])
        skus = by_style.get(style) or []
        want_colors = {str(s.get("color_name") or "").strip() for s in skus}
        want_sizes = {normalize_size(str(s.get("size_name") or "")) for s in skus}

        # What the parent's CHILDREN already cover -- the practical grid.
        try:
            kids = client.suiteql(
                f"SELECT {COLOR_FIELD} AS c, {SIZE_FIELD} AS sz "
                f"FROM item WHERE parent = {pid}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{style}: child read failed -- {str(exc)[:90]}")
            continue
        have_colors = {str(k.get("c") or "") for k in kids if k.get("c")}
        have_sizes = {str(k.get("sz") or "") for k in kids if k.get("sz")}

        print(f"=== {style} (parent id {pid}): {len(kids)} existing child(ren)")
        print(f"    S&S wants {len(want_colors)} colour(s), {len(want_sizes)} size(s); "
              f"children currently span {len(have_colors)} colour id(s), "
              f"{len(have_sizes)} size id(s)")
        covered["styles"] += 1
        covered["children"] += len(kids)

    print("\nWhat this tells the create path:")
    print("  * if the RAW parent row shows a populated colour/size value, the")
    print("    grid is explicit and creation must EXTEND it before posting a")
    print("    child in a new colour;")
    print("  * if it is empty while children exist, the grid is implied by the")
    print("    children and the RESTlet's name-resolution adoption is enough.")
    print("\nread-only probe: nothing was written to NetSuite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
