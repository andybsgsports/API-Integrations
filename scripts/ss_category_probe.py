"""Where does S&S keep a style's category? -- read-only.

S&S-created parents get no Class, and the create run's diagnostic showed why:
the category arrives EMPTY, not unmapped (run 31654887046 tallied every
missing class under ``''``). ``style_from_payload`` reads ``categoryName``
from ``/Styles`` and ``product_from_payload`` reads it from ``/Products``;
if the API spells it differently -- or only exposes a category id, or a
separate endpoint -- both come back blank and every S&S item is classless.

So: print the RAW keys of one style row and one product row, plus any
key whose name mentions categ/type/group, before assuming anything. Same
discipline as the swatch and parent-grid probes -- the representation is
discovered here, not guessed.
"""

from __future__ import annotations

import json

from ss_activewear_netsuite.config import get_config
from ss_activewear_netsuite.ss_activewear.client import SsClient


def _show(label: str, row: dict) -> None:
    print(f"\n=== RAW {label} keys ({len(row)}):")
    print("  " + ", ".join(sorted(row)))
    hits = {k: v for k, v in row.items()
            if any(w in k.lower() for w in ("categ", "type", "group", "class"))}
    print(f"  category-ish fields: {json.dumps(hits, default=str)[:600]}")


def main() -> int:
    cfg = get_config()
    styles: list = []
    client = SsClient(cfg.ss_api)

    # /Styles -- one unpaged response; take the first row RAW.
    try:
        styles = client._get("/Styles")  # noqa: SLF001 - probing the wire shape
        if styles:
            _show("/Styles row", dict(styles[0]))
            with_cat = [s for s in styles
                        if str(s.get("categoryName") or "").strip()]
            print(f"  {len(with_cat)} of {len(styles)} style(s) carry a "
                  f"non-empty categoryName")
    except Exception as exc:  # noqa: BLE001
        print(f"/Styles probe failed: {str(exc)[:200]}")

    # /Products for one style -- the per-SKU shape.
    try:
        sid = str(styles[0].get("styleID") or "") if styles else ""
        prods = client._get(f"/Products?styleid={sid}") if sid else []
        if prods:
            _show(f"/Products row (styleID {sid})", dict(prods[0]))
    except Exception as exc:  # noqa: BLE001
        print(f"/Products probe failed: {str(exc)[:200]}")

    # Is there a categories endpoint at all?
    for path in ("/Categories", "/categories"):
        try:
            cats = client._get(path)  # noqa: SLF001
            print(f"\n{path}: {len(cats)} row(s); first: "
                  f"{json.dumps(cats[0], default=str)[:300] if cats else '-'}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"{path}: {str(exc)[:120]}")

    print("\nread-only probe: nothing was written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
