"""Create the Momentec items NetSuite is missing -- parents, then children.

Momentec (Augusta Sportswear, Holloway, High Five, Russell, Badger Sport...)
had an update path only: the nightly kept ~900 matched items current and never
added anything, so a new style or colour had to be keyed by hand. This is the
create path (Andy, 2026-11-18).

One pass over the standard product feed, one style at a time:

* **new** -- nothing in NetSuite carries the style as its Vendor Name/Code:
  the matrix parent is created, then every child.
* **adopt** -- the style exists: only the feed SKUs with no matching item are
  added, under the existing parent (sent by id, never by name).
* **skip, and list it** -- anything that does not fit, never guessed:
  - an item is already named like the style but is not this vendor's;
  - the style has no single parent (none, or more than one);
  - the style has existing items that could not be matched to the feed (a
    colour spelled differently could be the "missing" one, and adding it
    would make a duplicate);
  - a SKU whose UPC is already on another item;
  - a colour or size that is on the BSG list only as an inactive value.

Only feed rows whose Status is in ``MOMENTEC_CREATE_STATUSES`` (default
``20``, the active status; closeouts and the other codes are counted and left
alone) are created. The sublimation feed is read so its items can be
recognised, but its styles are not created unless
``MOMENTEC_CREATE_SUBLIMATION=true``.

Items are born in Andy's item format: ``{style}-{Color}-{Size}`` names, Title
Case colours and names, Display Name = Description, dealer cost in the item's
own Purchase Price and on the vendor line, UPC, location Badger Sporting
Goods, Manufacturer = the brand, Preferred Vendor = Momentec, Base Price =
the feed's MSRP.

``CREATE_MAX_STYLES`` caps net-new styles per run and ``UPDATE_MAX_ITEMS``
caps children added under existing parents. Honours ``SYNC_DRY_RUN``: a dry
run reports exactly what it would create and writes nothing.
"""

from __future__ import annotations

import html
import os
import re
from collections import Counter
from pathlib import Path

from momentec_backfill import (
    BRAND_NAMES,
    _net_cost,
    _num,
    _weight_lb,
    fetch,
)
from native_pricing import base_price
from pricing_ownership import VENDOR_MOMENTEC
from run_status import exit_code
from sanmar_parent_create import (
    DEFAULT_ASSET_ACCOUNT,
    DEFAULT_COGS_ACCOUNT,
    DEFAULT_DEPARTMENT,
    DEFAULT_INCOME_ACCOUNT,
    DEFAULT_LOCATION,
    DEFAULT_SUBSIDIARY,
    DEFAULT_TAX_SCHEDULE,
    _class_id,
    _create_parent,
    _uom_ids,
    resolve_parent_refs,
)

from momentec_netsuite.adopt import clean_color
from momentec_netsuite.config import get_config
from momentec_netsuite.feeds import parse_product_data
from momentec_netsuite.models import MomentecSku
from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.adopt import COLOR_FIELD, SIZE_FIELD, OptionMaps
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import (
    COLOR_LIST,
    SIZE_LIST,
    MatrixOptionResolver,
    normalize_option_name,
)
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.sizes import normalize_size

RESTLET_BATCH = 25
EXTERNAL_PREFIX = "MTEC-"
#: Cap on named lines per skip reason, so the report stays readable.
LIST_MAX = 40

#: Short tokens that are real words, not abbreviations.
_SHORT_WORDS = {"RED", "TAN", "SKY", "HOT", "ICE", "ASH", "JET", "OAT", "NBA"}
#: Longer tokens the feed uses as abbreviations or cuts off mid-word.
_KNOWN_ABBREVIATIONS = {
    "GRAPH", "CHAR", "HEATH", "ORAN", "OCEA", "THUN", "SGMA", "BKLYN",
    "BUCCHOBU", "BUCKHBUC", "BUCORBUC", "BUCWHBUC",
}
#: Tokens that keep their capitals in Title Case.
_ACRONYMS = {"NBA"}
#: The feed cuts a colour name off at this many characters.
_FEED_COLOR_WIDTH = 30

_WORD = re.compile(r"[A-Za-z][A-Za-z']*")


def title_case(text: str) -> str:
    """ALL CAPS feed text in Title Case; mixed-case text is left as written.

    ``YOUTH B-CORE LONG SLEEVE HOODIE`` -> ``Youth B-Core Long Sleeve Hoodie``.
    A token containing a digit (``C2``, ``2XL``) keeps its capitals.
    """
    t = " ".join(html.unescape(text or "").split())
    if not t or t != t.upper():
        return t
    out: list[str] = []
    for token in t.split(" "):
        if any(ch.isdigit() for ch in token):
            out.append(token)
        else:
            out.append(_WORD.sub(
                lambda m: m.group(0) if m.group(0) in _ACRONYMS
                else m.group(0).capitalize(), token))
    return " ".join(out)


def color_name(feed_color: str) -> str:
    """``NAVY/WHITE (BA)`` -> ``Navy/White``: brand tag off, Title Case."""
    return title_case(clean_color(feed_color))


def abbreviated_color(feed_color: str) -> str:
    """Why a feed colour is not a clean, spelled-out name ('' when it is).

    The BSG colour list is being cleaned of abbreviations (Dgh, Mgnt...), and
    the feed has its own: ``BT. ORANGE/WHITE``, ``COL BL/BLACK/WHITE``,
    ``HTHR GREY``, plus names cut off at the feed's column width. Adding those
    as new list values would undo that cleanup, and spelling them out would be
    a guess -- so such a SKU is skipped and listed for Andy.
    """
    c = clean_color(feed_color).upper()
    if "." in c:
        return "has an abbreviation with a period"
    if len(c) >= _FEED_COLOR_WIDTH:
        return "is cut off in the feed"
    for token in re.split(r"[^A-Z]+", c):
        if not token:
            continue
        if len(token) <= 3 and token not in _SHORT_WORDS:
            return f"has the abbreviation {token!r}"
        if token in _KNOWN_ABBREVIATIONS or not re.search(r"[AEIOUY]", token):
            return f"has the abbreviation {token!r}"
    return ""


def class_path(category: str, name: str = "") -> str:
    """NetSuite Class for a feed category such as ``Adult | TEES | TOPS FSG``.

    The feed's three segments are audience | family | group. The group says
    whether it is an on-field uniform piece, and the item name settles shorts
    against pants. Returns '' where neither is certain (accessories), so the
    parent is listed rather than guessed.
    """
    segs = [t.strip().lower() for t in (category or "").split("|")]
    family = segs[1] if len(segs) > 1 else ""
    group = segs[2] if len(segs) > 2 else ""
    n = (name or "").lower()
    is_short = "short" in n
    is_pant = any(w in n for w in ("pant", "knicker", "jogger", "legging", "tight"))
    if "sock" in n:
        return "Uniforms : Socks"
    if family == "headwear":
        return "Uniforms : Headwear"
    if family == "bags":
        return "Bags"
    if group.startswith(("on-field tops", "on-fld tops")):
        return "Uniforms : Jersey"
    if group.startswith(("on-field bottoms", "on-fld bottoms")):
        return ("Uniforms : Shorts" if is_short
                else "Uniforms : Pants" if is_pant else "Uniforms")
    if "bottom" in group or family == "bottoms":
        return ("Bottoms : Shorts" if is_short
                else "Bottoms : Pants" if is_pant else "Bottoms")
    if family == "pullovers":
        return "Outerwear : Pullovers"
    if family == "outerwear" or "outerwear" in group:
        return "Outerwear : Vests" if "vest" in n else "Outerwear : Jackets"
    if family == "tees":
        return "Tops : Tees"
    if family == "polos":
        return "Tops : Polos"
    if family == "fleece":
        if "1/4 zip" in n or "quarter zip" in n:
            return "Tops : 1/4 Zips"
        return "Tops : Hoods" if "hood" in n else "Tops : Sweatshirts"
    if "tops" in group:
        return "Tops"
    return ""


def style_token(sku: MomentecSku) -> str:
    return sku.item_sku.split(".")[0]


def creatable(sku: MomentecSku, statuses: set[str]) -> bool:
    return sku.status in statuses


def plan_style(skus: list[MomentecSku], rows: list[dict], options,
               statuses: set[str]) -> dict:
    """Decide what to do with one style.

    ``rows`` are the NetSuite items carrying the style as Vendor Name/Code
    (id, itemid, color, size). Returns ``{"kind", "reason", "missing",
    "parent_id"}`` where kind is new / adopt / complete / skip.
    """
    wanted = [s for s in skus if creatable(s, statuses)]
    if not rows:
        if not wanted:
            return {"kind": "complete", "reason": "", "missing": [], "parent_id": None}
        return {"kind": "new", "reason": "", "missing": wanted, "parent_id": None}

    opt_index: dict[tuple[str, str], str] = {}
    children: set[str] = set()
    parent_rows: list[str] = []
    parents_of_children: set[str] = set()
    for r in rows:
        rid = str(r["id"])
        color_id, size_id = str(r.get("color") or ""), str(r.get("size") or "")
        if color_id and size_id:
            opt_index.setdefault((color_id, size_id), rid)
            children.add(rid)
            if str(r.get("parent") or ""):
                parents_of_children.add(str(r["parent"]))
        else:
            parent_rows.append(rid)
    # The parent is whichever item the existing children sit under. It often
    # does not carry the style as its own Vendor Name/Code (160 styles in the
    # 2026-11-18 sandbox dry run), and where a style has duplicate parents the
    # one holding the children is the real one. With no children yet, a single
    # parent row carrying the vendor code is the parent.
    parents = sorted(parents_of_children) if children else parent_rows

    claimed: set[str] = set()
    missing: list[MomentecSku] = []
    for sku in skus:
        color = clean_color(sku.color)
        size_ids = list(dict.fromkeys(
            options.size_candidates(sku.size)
            + options.size_candidates(normalize_size(sku.size))))
        hit = None
        for color_id, _method in options.color_candidates(color, color):
            for size_id in size_ids:
                hit = opt_index.get((color_id, size_id))
                if hit:
                    break
            if hit:
                break
        if hit:
            claimed.add(hit)
        elif creatable(sku, statuses):
            missing.append(sku)

    if not missing:
        return {"kind": "complete", "reason": "", "missing": [], "parent_id": None}
    if len(parents) != 1:
        return {"kind": "skip", "missing": missing, "parent_id": None,
                "reason": (f"its existing items sit under {len(parents)} "
                           f"different parents" if children
                           else f"{len(parents)} parent item(s) carry this "
                                f"vendor code and none has children")}
    unclaimed = len(children - claimed)
    if unclaimed:
        return {"kind": "skip", "missing": missing, "parent_id": None,
                "reason": f"{unclaimed} existing item(s) could not be matched "
                          f"to the feed"}
    return {"kind": "adopt", "reason": "", "missing": missing,
            "parent_id": parents[0]}


def fetch_style_items(client, tokens: list[str]) -> dict[str, list[dict]]:
    """style -> items carrying it as Vendor Name/Code (option ids and parent).

    One query per 40 styles, not one per style: the first sandbox dry run
    spent 37 minutes on ~1,900 single lookups while the nightly was writing,
    and the second died when one of them was throttled past its retries.
    """
    out: dict[str, list[dict]] = {t: [] for t in tokens}
    by_upper = {t.upper(): t for t in tokens}
    for i in range(0, len(tokens), 40):
        chunk = tokens[i:i + 40]
        in_list = ", ".join(f"'{_sql_escape(t)}'" for t in chunk)
        for r in client.suiteql(
            f"SELECT id, itemid, parent, vendorname, {COLOR_FIELD} AS color, "
            f"{SIZE_FIELD} AS size FROM item WHERE vendorname IN ({in_list})"
        ):
            key = by_upper.get(str(r.get("vendorname") or "").upper())
            if key is not None:
                out[key].append(r)
    return out


def on_list(resolver, list_type: str, name: str) -> bool:
    """Is ``name`` already a value on the list (any spelling variant)?

    Answered from the resolver's one-query-per-list index, never a query per
    name.
    """
    key = normalize_option_name((name or "").strip())
    return bool(key) and key in resolver._norm_index(list_type)


def child_fields(sku: MomentecSku) -> dict[str, object]:
    """The ``custitem_mtec_*`` data a child is born with."""
    guide = sku.size_chart_url
    if guide.startswith("http://"):
        guide = "https://" + guide[len("http://"):]
    out: dict[str, object] = {
        "custitem_mtec_item_sku": sku.item_sku,
        "custitem_mtec_style": style_token(sku),
        "custitem_mtec_gtin": sku.gtin,
        "custitem_mtec_msrp": _num(sku.msrp),
        "custitem_mtec_cost": _net_cost(_num(sku.cost)),
        "custitem_mtec_case_size": (int(sku.case_pack_qty)
                                    if sku.case_pack_qty.isdigit() else None),
        "custitem_mtec_size_guide": guide,
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def child_payload(sku: MomentecSku, resolver, uom: dict[str, str],
                  parent_id: str | None = None) -> dict[str, object]:
    """One matrix RESTlet child payload, colour and size in the LIST's spelling."""
    style = style_token(sku)
    color = resolver.canonical_name(COLOR_LIST, color_name(sku.color))
    size = resolver.canonical_name(SIZE_LIST, normalize_size(sku.size))
    name = title_case(sku.name) or style
    weight, weight_unit = _weight_lb(sku.weight, sku.weight_unit)
    payload: dict[str, object] = {
        "externalId": f"{EXTERNAL_PREFIX}{sku.item_sku}",
        "itemId": f"{style}-{color}-{size}",
        "style": style,
        "parentId": parent_id,
        "color": color,
        "size": size,
        "vendorName": style,
        "upc": sku.gtin or sku.upc,
        "cost": _net_cost(_num(sku.cost)),
        "basePrice": base_price(_num(sku.msrp)),
        "weight": weight,
        "manufacturer": BRAND_NAMES.get(sku.brand),
        "preferredVendorId": str(VENDOR_MOMENTEC),
        "costingMethod": "AVG",
        "displayName": name[:60],
        "description": name,
        "shopImageUrl": sku.main_image_url if sku.main_image_url.startswith(
            "https://") else None,
        "incomeAccount": DEFAULT_INCOME_ACCOUNT,
        "cogsAccount": DEFAULT_COGS_ACCOUNT,
        "assetAccount": DEFAULT_ASSET_ACCOUNT,
        "taxSchedule": DEFAULT_TAX_SCHEDULE,
        "subsidiary": DEFAULT_SUBSIDIARY,
        "department": DEFAULT_DEPARTMENT,
        "location": DEFAULT_LOCATION,
        "preferredLocation": DEFAULT_LOCATION,
        "fields": child_fields(sku),
    }
    if weight_unit:
        payload["weightUnitId"] = (weight_unit.get("id")
                                   if isinstance(weight_unit, dict) else weight_unit)
    for key in ("unitsTypeId", "stockUnitId", "purchaseUnitId", "saleUnitId"):
        if uom.get(key):
            payload[key] = uom[key]
    return {k: v for k, v in payload.items() if v not in (None, "")}


def parent_body(style: str, skus: list[MomentecSku], uom: dict[str, str],
                parent_refs: dict[str, dict]) -> dict[str, object]:
    first = skus[0]
    name = title_case(first.name) or style
    body: dict[str, object] = {
        "itemId": style, "vendorName": style,
        "matrixType": "PARENT", "isInactive": False, "isOnline": False,
        "itemVendor": {"items": [{
            "vendor": {"id": str(VENDOR_MOMENTEC)},
            "preferredVendor": True,
            "vendorCode": style,
        }]},
        "displayName": name[:60],
        "storeDisplayName": name,
        "salesDescription": name,
        "purchaseDescription": name,
        "storeDescription": store_description(first.description),
        **{k: {"id": v} for k, v in {
            "unitsType": uom.get("unitsTypeId", ""),
            "stockUnit": uom.get("stockUnitId", ""),
            "purchaseUnit": uom.get("purchaseUnitId", ""),
            "saleUnit": uom.get("saleUnitId", ""),
        }.items() if v},
        **parent_refs,
    }
    brand = BRAND_NAMES.get(first.brand)
    if brand:
        body["manufacturer"] = brand
    return {k: v for k, v in body.items() if v not in (None, "")}


def store_description(text: str) -> str:
    """The feed's ``* point * point`` description as "- " lines."""
    t = html.unescape(text or "")
    parts = [p.strip() for p in t.split("*") if p.strip()]
    if len(parts) <= 1:
        return " ".join(t.split())
    return "\n".join(f"- {p}" for p in parts)


def post_children(client, cfg, payloads: list[dict]) -> tuple[int, int, int, list[str]]:
    """Post payloads through the matrix RESTlet.

    Returns ``(created, already_present, failures, name_taken)``. An existing
    option combination is idempotency; an item name that is already taken is a
    skip for the list (Andy, 2026-11-12), not a failure.
    """
    created = already = failures = 0
    name_taken: list[str] = []
    for i in range(0, len(payloads), RESTLET_BATCH):
        batch = payloads[i:i + RESTLET_BATCH]
        by_ext = {str(p.get("externalId")): p for p in batch}
        try:
            resp = client.call_restlet(
                cfg.netsuite.matrix_script_id, cfg.netsuite.matrix_deploy_id,
                {"items": batch},
            )
        except Exception as exc:  # noqa: BLE001
            failures += len(batch)
            print(f"  RESTLET CALL FAILED: {str(exc)[:200]}")
            continue
        for r in resp.get("results", []):
            if r.get("status") != "error":
                created += 1
                continue
            msg = str(r.get("message") or "")
            low = msg.lower()
            if "combination of options already exists" in low:
                already += 1
            elif "already exists" in low or "uniquely identify" in low:
                item = by_ext.get(str(r.get("externalId")), {}).get("itemId", "")
                name_taken.append(str(item))
                print(f"  CHILD SKIPPED {r.get('externalId')} [{item}]: "
                      f"an item with that name already exists")
            else:
                failures += 1
                print(f"  CHILD FAILED {r.get('externalId')}: {msg[:140]}")
    return created, already, failures, name_taken


def _existing_names(client, tokens: list[str]) -> set[str]:
    """Upper-cased item names already taken among ``tokens``."""
    taken: set[str] = set()
    for i in range(0, len(tokens), 200):
        chunk = ", ".join(f"'{_sql_escape(t.upper())}'" for t in tokens[i:i + 200])
        for r in client.suiteql(
            f"SELECT itemid FROM item WHERE UPPER(itemid) IN ({chunk})"
        ):
            taken.add(str(r.get("itemid") or "").upper())
    return taken


def _upcs_on_file(client, skus: list[MomentecSku]) -> set[str]:
    codes = sorted({c for s in skus for c in (s.gtin, s.upc) if c})
    found: set[str] = set()
    for i in range(0, len(codes), 200):
        chunk = ", ".join(f"'{_sql_escape(c)}'" for c in codes[i:i + 200])
        for r in client.suiteql(
            f"SELECT upccode FROM item WHERE upccode IN ({chunk})"
        ):
            found.add(str(r.get("upccode") or ""))
    return found


def _show(title: str, lines: list[str]) -> None:
    if not lines:
        return
    print(f"\n{title}: {len(lines):,}")
    for line in lines[:LIST_MAX]:
        print(f"  SKIPPED {line}")
    if len(lines) > LIST_MAX:
        print(f"  ... and {len(lines) - LIST_MAX:,} more")


def main() -> int:  # noqa: PLR0912, PLR0915 - one ordered pass, like ss_create
    cfg = ns_config()
    mcfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_styles = int(os.environ.get("CREATE_MAX_STYLES", "0") or "0")
    max_children = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    statuses = {s.strip() for s in os.environ.get(
        "MOMENTEC_CREATE_STATUSES", "20").split(",") if s.strip()}
    with_sublimation = os.environ.get(
        "MOMENTEC_CREATE_SUBLIMATION", "").strip().lower() in ("1", "true", "yes")
    only = {s.strip().upper() for s in os.environ.get(
        "MOMENTEC_CREATE_STYLES", "").split(",") if s.strip()}
    dl = Path(mcfg.download_dir)

    std = parse_product_data([fetch(mcfg.products_url, dl / "product-data-std-all.csv")])
    sub = parse_product_data(
        [fetch(mcfg.sublimation_url, dl / "sublimation-product-data-std-all.csv")])
    by_style: dict[str, list[MomentecSku]] = {}
    for style in std + sub:
        for sku in style.skus:
            if style_token(sku) and len(sku.item_sku.split(".")) >= 3:
                by_style.setdefault(style_token(sku), []).append(sku)
    std_tokens = {style_token(k) for s in std for k in s.skus}
    tokens = sorted(by_style if with_sublimation else std_tokens & set(by_style))
    if only:
        tokens = [t for t in tokens if t.upper() in only]
    status_tally = Counter(k.status for t in tokens for k in by_style[t])
    print(f"feed: {len(tokens):,} style(s) in scope; status counts "
          f"{dict(status_tally)}; creating status {sorted(statuses)} only")

    client = NetSuiteClient(cfg.netsuite)
    options = OptionMaps(client)
    if not options.available:
        print("colour/size option lists could not be read -- nothing done")
        return 1
    resolver = MatrixOptionResolver(client=client, allow_create=allow_write)
    names_taken = _existing_names(client, tokens)

    uom: dict[str, str] = {}
    parent_refs: dict[str, dict] | None = None
    if allow_write:
        uom = _uom_ids(client)

    new_parents = new_children = adopt_children = already = failures = 0
    styles_new = styles_adopt = styles_complete = 0
    posted_adopt = 0
    skipped_styles: list[str] = []
    skipped_skus: list[str] = []
    new_colors: Counter[str] = Counter()
    abbreviated: Counter[str] = Counter()
    new_sizes: Counter[str] = Counter()
    unmapped_cats: Counter[str] = Counter()
    capped_new = capped_adopt = 0

    rows_by_style = fetch_style_items(client, tokens)
    for token in tokens:
        skus = by_style[token]
        plan = plan_style(skus, rows_by_style[token], options, statuses)
        kind = plan["kind"]
        if kind == "complete":
            styles_complete += 1
            continue
        if kind == "skip":
            skipped_styles.append(
                f"style {token}: {plan['reason']} "
                f"({len(plan['missing'])} SKU(s) not added)")
            continue
        if kind == "new" and token.upper() in names_taken:
            skipped_styles.append(
                f"style {token}: an item is already named {token} but does not "
                f"carry it as Vendor Name/Code ({len(plan['missing'])} SKU(s) "
                f"not added)")
            continue
        if kind == "new" and max_styles and styles_new >= max_styles:
            capped_new += 1
            continue
        if kind == "adopt" and max_children and posted_adopt >= max_children:
            capped_adopt += 1
            continue

        # Per-SKU guards: UPC already on another item, retired colour or size.
        try:
            on_file = _upcs_on_file(client, plan["missing"])
        except Exception as exc:  # noqa: BLE001 - one busy read must not end the run
            skipped_styles.append(
                f"style {token}: NetSuite could not be read just now "
                f"({str(exc)[:60]}); it is picked up on the next run")
            continue
        todo: list[MomentecSku] = []
        for sku in plan["missing"]:
            color, size = color_name(sku.color), normalize_size(sku.size)
            why = abbreviated_color(sku.color)
            if (sku.gtin and sku.gtin in on_file) or (sku.upc and sku.upc in on_file):
                skipped_skus.append(f"{sku.item_sku}: its UPC is already on another item")
            elif why and not on_list(resolver, COLOR_LIST, color):
                # Already on the BSG list under this exact name: nothing new
                # is added, so it is used. Otherwise it is listed for Andy.
                abbreviated[clean_color(sku.color)] += 1
                skipped_skus.append(
                    f"{sku.item_sku}: colour {clean_color(sku.color)!r} {why}")
            elif resolver.is_retired(COLOR_LIST, color):
                skipped_skus.append(
                    f"{sku.item_sku}: colour {color!r} is inactive on the BSG list")
            elif resolver.is_retired(SIZE_LIST, size):
                skipped_skus.append(
                    f"{sku.item_sku}: size {size!r} is inactive on the BSG list")
            else:
                todo.append(sku)
        if kind == "adopt" and max_children:
            todo = todo[:max_children - posted_adopt]
        if not todo:
            continue

        for sku in todo:
            c, s = color_name(sku.color), normalize_size(sku.size)
            if not on_list(resolver, COLOR_LIST, c):
                new_colors[c] += 1
            if not on_list(resolver, SIZE_LIST, s):
                new_sizes[s] += 1
        if allow_write:
            # Create the genuinely new values before any child references them.
            for sku in todo:
                resolver.resolve(COLOR_LIST, color_name(sku.color))
                resolver.resolve(SIZE_LIST, normalize_size(sku.size))

        first = todo[0]
        print(f"\n=== {kind} {token}: {len(todo)} child(ren) "
              f"[{BRAND_NAMES.get(first.brand, first.brand)}] "
              f"{title_case(first.name)!r}")
        parent_id = plan["parent_id"]
        if kind == "new":
            styles_new += 1
            cls = class_path(first.category, first.name)
            if not cls:
                unmapped_cats[first.category] += 1
            if allow_write:
                if parent_refs is None:
                    parent_refs = resolve_parent_refs(client)
                body = parent_body(token, todo, uom, parent_refs)
                cls_id = _class_id(client, cls) if cls else None
                if cls_id:
                    body["class"] = {"id": cls_id}
                try:
                    parent_id = str(_create_parent(client, body) or "") or None
                    new_parents += 1
                    print(f"  parent created: id {parent_id or '?'}")
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"  PARENT FAILED {token}: {str(exc)[:150]} "
                          f":: {str(getattr(exc, 'payload', ''))[:300]}")
                    continue
            else:
                new_parents += 1
                print(f"  WOULD create parent {token!r} "
                      f"({title_case(first.name)[:60]!r}, class {cls or 'none'})")
        else:
            styles_adopt += 1

        payloads = [child_payload(s, resolver, uom, parent_id) for s in todo]
        if kind == "adopt":
            posted_adopt += len(payloads)
        if not allow_write:
            if kind == "new":
                new_children += len(payloads)
            else:
                adopt_children += len(payloads)
            print(f"  WOULD post {len(payloads)} child(ren), e.g. "
                  f"{payloads[0].get('itemId')!r} cost {payloads[0].get('cost')} "
                  f"base {payloads[0].get('basePrice')}")
            continue
        c, a, f, taken = post_children(client, cfg, payloads)
        if kind == "new":
            new_children += c
        else:
            adopt_children += c
        already += a
        failures += f
        skipped_skus += [f"{n}: an item with that name already exists" for n in taken]

    _show("styles skipped", skipped_styles)
    _show("SKUs skipped", skipped_skus)
    for label, counter in (("colour", new_colors), ("size", new_sizes)):
        if counter:
            verb = "added to" if allow_write else "WOULD be added to"
            print(f"\nnew {label} value(s) {verb} the BSG list: {len(counter)}")
            for value, n in counter.most_common(LIST_MAX):
                print(f"  {value!r} ({n} SKU(s))")
    if abbreviated:
        print(f"\ncolour(s) skipped as abbreviated or cut off, for Andy to "
              f"decide: {len(abbreviated)}")
        for value, n in abbreviated.most_common(LIST_MAX):
            print(f"  {value!r} ({n} SKU(s))")
    if unmapped_cats:
        print("\ncategories with no class mapping (parent left without a class):")
        for cat, n in unmapped_cats.most_common(20):
            print(f"  {n:>4}x {cat!r}")
    if capped_new or capped_adopt:
        print(f"\nNOTE: momentec create left {capped_new:,} new style(s) and "
              f"{capped_adopt:,} existing style(s) for a later run (caps: "
              f"{max_styles or 'none'} new styles, {max_children or 'none'} "
              f"children under existing parents)")

    verb = "created" if allow_write else "WOULD create (dry run)"
    print(f"\nmomentec create: {verb} {new_parents} parent(s), {new_children} "
          f"child(ren) under them, {adopt_children} child(ren) under "
          f"{styles_adopt} existing parent(s); styles with nothing to add: "
          f"{styles_complete}; styles skipped: {len(skipped_styles)}; SKUs "
          f"skipped: {len(skipped_skus)}; already present: {already}; "
          f"failures: {failures}")
    return exit_code("momentec create", failures, failures,
                     new_children + adopt_children + failures)


if __name__ == "__main__":
    raise SystemExit(main())
