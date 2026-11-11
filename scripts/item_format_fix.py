"""One-time corrections that bring EXISTING items up to Andy's item format.

Andy's sandbox review (2026-11-11) of the 155,342 items the pipeline had
created found them structurally right (names, matrix links, vendor, accounts)
but off-format in five ways. The create path now births items correctly; this
script repairs what is already there. Each step is independent, diff-aware,
honours ``SYNC_DRY_RUN`` and is capped by ``UPDATE_MAX_ITEMS`` (per step), so
it can be proven on a small batch before a full run.

Steps (``FORMAT_FIX_STEPS``, comma list; default = the first four):

``displayname``
    "Display name should not have the style number at the end. match
    description." Strips the trailing style code the 2026-08-05 spec put on
    Display Name (106,596 items) and uses the Sales Description wherever it is
    the same title.
``template``
    Parent matrix name template -> ``{itemid}-{color}-{size}``. NetSuite's
    default put size first on 3,927 parents, so a child added by hand later
    would be named Style-Size-Color.
``vendorprice``
    Fills the PREFERRED vendor line's blank Purchase Price from the item's
    cost (126,781 blank lines). Fill-blanks-only: a price someone entered on a
    vendor line is never overwritten.
``teamprice``
    Suggested Team Price (level 4) = 90% of Base on items that have a Base
    Price but no Team price (~115,700). Goes through the matrix RESTlet, the
    only writer that can add a price-level line to an existing item (a REST
    PATCH can only edit a line that already exists). The first batch is read
    back; if NetSuite kept none of it the step stops and says so, rather than
    reporting 115,000 writes that did not happen.
``manufacturer``
    Fills blank Manufacturer (and blank UPC, when the feed carries a GTIN) on
    SanMar items the nightly update cannot reach because it matches by UPC.
    Needs the SanMar feed, so it is opt-in.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from decimal import ROUND_HALF_UP, Decimal

from concurrent_writes import write_records
from run_status import exit_code

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

DEFAULT_STEPS = ("displayname", "template", "vendorprice", "teamprice")
ALL_STEPS = (*DEFAULT_STEPS, "manufacturer")

#: Andy's standing rule: a matrix child is named Style-Color-Size, colour
#: BEFORE size. NetSuite's own default for a new parent put size first, so a
#: child added later by hand on the parent's Matrix subtab came out
#: Style-Size-Color (3,927 of 5,255 sandbox parents, 2026-11-11).
MATRIX_NAME_TEMPLATE = "{itemid}-{custitem_bsg_color}-{custitem_bsg_size}"
BASE_PRICE_LEVEL = 1
TEAM_PRICE_LEVEL = 4
DISPLAY_NAME_MAX = 60  # the length the create path has always written
ID_WINDOW = 50_000     # SuiteQL caps one result set at 100k rows
RESTLET_BATCH = 25


# --- pure rules (unit-tested) -------------------------------------------------

def strip_style_suffix(display_name: str, style: str) -> str:
    """``'Sport-Tek 1/2-Zip Wind Shirt JST75'`` -> ``'Sport-Tek 1/2-Zip Wind Shirt'``.

    Only a trailing, whole-word style code is removed (case-insensitive); a
    name that merely contains the code elsewhere, or IS the bare code, is left
    alone -- a Display Name is never emptied.
    """
    name = (display_name or "").strip()
    code = (style or "").strip()
    if not name or not code:
        return name
    stripped = re.sub(rf"[\s.,-]+{re.escape(code)}[\s.]*$", "", name, flags=re.I)
    stripped = stripped.strip(" .,-")
    return stripped or name


def display_target(display_name: str, description: str, style: str) -> str:
    """The Display Name an item should carry, or its current one if already right.

    Strip the style code; then, if the Sales Description is the same title
    (the stripped name is its beginning, as when the old 60-character cut
    truncated it) and fits, use the description exactly -- "match description".

    The old cut could also land INSIDE the code: style YT565 was stored as
    "Sport-Tek Youth PosiCharge Mesh Reversible Spliced Short YT5". A trailing
    word that is the start of the style code is treated as that same suffix,
    but only when the Sales Description confirms the rest is the title.
    """
    current = (display_name or "").strip()
    stripped = strip_style_suffix(current, style)
    desc = (description or "").strip()
    code = (style or "").strip()
    if stripped == current and code and " " in current:
        head, tail = current.rsplit(" ", 1)
        head = head.strip(" .,-")
        if (len(tail) >= 2 and code.casefold().startswith(tail.casefold())
                and desc and desc.casefold().startswith(head.casefold())):
            stripped = head
    if (desc and len(desc) <= DISPLAY_NAME_MAX
            and desc.casefold().startswith(stripped.casefold())):
        return desc
    return stripped


def template_needs_fix(template: str | None) -> bool:
    """True for a two-option template that is not Color-then-Size.

    Single-option parents (size only / colour only) and hand-written odd
    templates are left alone and reported, not rewritten.
    """
    t = (template or "").strip()
    if not t or t == MATRIX_NAME_TEMPLATE:
        return False
    tokens = re.findall(r"\{[^}]+\}", t)
    return sorted(tokens) == sorted(re.findall(r"\{[^}]+\}", MATRIX_NAME_TEMPLATE))


def team_price(base: float | str | None) -> float | None:
    """Suggested Team Price: 90% of Base, to the cent; None without a Base."""
    if base in (None, ""):
        return None
    value = Decimal(str(base))
    if value <= 0:
        return None
    return float((value * Decimal("0.9")).quantize(Decimal("0.01"), ROUND_HALF_UP))


def vendor_lines_with_price(lines: list[dict], cost: float | str | None) -> list[dict] | None:
    """REST ``itemVendor`` lines with the PREFERRED line's blank price filled.

    Returns None when there is nothing to do: no cost, no preferred line, or
    the preferred line already has a price. Every existing line is carried
    over unchanged (vendor, code, preferred flag, any price already on it).
    """
    if cost in (None, "") or float(cost) <= 0:
        return None
    out: list[dict] = []
    changed = False
    for line in lines:
        preferred = str(line.get("preferredvendor") or "").upper() in ("T", "TRUE", "1")
        body: dict[str, object] = {
            "vendor": {"id": str(line["vendor"])},
            "preferredVendor": preferred,
        }
        if str(line.get("vendorcode") or "").strip():
            body["vendorCode"] = str(line["vendorcode"])
        price = line.get("purchaseprice")
        if price not in (None, ""):
            body["purchasePrice"] = float(price)
        elif preferred:
            body["purchasePrice"] = float(cost)
            changed = True
        out.append(body)
    return out if changed else None


# --- NetSuite plumbing --------------------------------------------------------

def _windows(client: NetSuiteClient) -> Iterator[tuple[int, int]]:
    rows = client.suiteql("SELECT MAX(id) AS m FROM item")
    top = int((rows[0].get("m") if rows else 0) or 0)
    for lo in range(0, top + 1, ID_WINDOW):
        yield lo, lo + ID_WINDOW


def _scan(client: NetSuiteClient, select: str, where: str, alias: str = "") -> Iterator[dict]:
    col = f"{alias}.id" if alias else "id"
    for lo, hi in _windows(client):
        yield from client.suiteql(
            f"{select} WHERE {col} > {lo} AND {col} <= {hi} AND ({where})"
        )


def _patch(client, jobs: list[tuple[str, dict]], allow_write: bool, label: str) -> int:
    """Write ``jobs`` (or count them on a dry run); returns failures."""
    verb = "wrote" if allow_write else "WOULD write (dry run)"
    if not allow_write:
        print(f"{label}: {verb} {len(jobs):,}")
        return 0
    shown = [0]

    def _on_err(rid: str, exc: Exception) -> None:
        shown[0] += 1
        if shown[0] <= 10:
            print(f"  FAILED item {rid}: {str(exc)[:150]} "
                  f":: {str(getattr(exc, 'payload', ''))[:200]}")

    written, failures = write_records(client, "inventoryItem", jobs, on_error=_on_err)
    print(f"{label}: {verb} {written:,}; failures: {failures:,}")
    return failures


def _capped(jobs: list, max_items: int, label: str) -> list:
    if max_items and len(jobs) > max_items:
        print(f"{label}: {len(jobs):,} need the change; capped to {max_items:,} this run")
        return jobs[:max_items]
    print(f"{label}: {len(jobs):,} need the change")
    return jobs


# --- steps --------------------------------------------------------------------

def fix_display_names(client, allow_write: bool, max_items: int) -> int:
    jobs: list[tuple[str, dict]] = []
    for r in _scan(
        client,
        "SELECT id, displayname, description, vendorname FROM item",
        # The style code on the end -- or a display name at the old 60-char
        # cut, where the code may have been truncated (display_target decides).
        "vendorname IS NOT NULL AND (UPPER(displayname) LIKE '% ' || UPPER(vendorname) "
        f"OR LENGTH(displayname) >= {DISPLAY_NAME_MAX})",
    ):
        current = str(r.get("displayname") or "")
        target = display_target(current, str(r.get("description") or ""),
                                str(r.get("vendorname") or ""))
        if target and target != current:
            if len(jobs) < 5:
                print(f"  e.g. item {r['id']}: {current!r} -> {target!r}")
            jobs.append((str(r["id"]), {"displayName": target}))
    return _patch(client, _capped(jobs, max_items, "display name"), allow_write,
                  "display name")


def fix_templates(client, allow_write: bool, max_items: int) -> int:
    jobs: list[tuple[str, dict]] = []
    left_alone: dict[str, int] = {}
    for r in client.suiteql(
        "SELECT id, itemid, matrixitemnametemplate FROM item WHERE matrixtype = 'PARENT'"
    ):
        template = str(r.get("matrixitemnametemplate") or "")
        if template_needs_fix(template):
            jobs.append((str(r["id"]), {"matrixItemNameTemplate": MATRIX_NAME_TEMPLATE}))
        elif template != MATRIX_NAME_TEMPLATE:
            left_alone[template or "(blank)"] = left_alone.get(template or "(blank)", 0) + 1
    for template, n in sorted(left_alone.items(), key=lambda kv: -kv[1]):
        print(f"  left alone ({n:,} parent(s)): {template}")
    return _patch(client, _capped(jobs, max_items, "name template"), allow_write,
                  "name template")


def fix_vendor_prices(client, allow_write: bool, max_items: int) -> int:
    by_item: dict[str, list[dict]] = {}
    cost_by_item: dict[str, object] = {}
    for r in _scan(
        client,
        "SELECT iv.item, iv.vendor, iv.vendorcode, iv.preferredvendor, "
        "iv.purchaseprice, i.cost FROM itemvendor iv JOIN item i ON i.id = iv.item",
        "i.cost IS NOT NULL",
        alias="i",
    ):
        rid = str(r["item"])
        by_item.setdefault(rid, []).append(r)
        cost_by_item[rid] = r.get("cost")
    jobs: list[tuple[str, dict]] = []
    for rid, lines in by_item.items():
        new_lines = vendor_lines_with_price(lines, cost_by_item.get(rid))
        if new_lines:
            jobs.append((rid, {"itemVendor": {"items": new_lines}}))
    return _patch(client, _capped(jobs, max_items, "vendor line price"), allow_write,
                  "vendor line price")


def fix_team_prices(client, cfg, allow_write: bool, max_items: int) -> int:
    todo: list[tuple[str, str, float]] = []  # (internal id, external id, team price)
    for r in _scan(
        client,
        "SELECT i.id, i.externalid, p.unitprice FROM item i "
        f"JOIN pricing p ON p.item = i.id AND p.pricelevel = {BASE_PRICE_LEVEL}",
        "i.externalid IS NOT NULL AND p.unitprice > 0 AND NOT EXISTS ("
        f"SELECT 1 FROM pricing t WHERE t.item = i.id AND t.pricelevel = {TEAM_PRICE_LEVEL} "
        "AND t.unitprice > 0)",
        alias="i",
    ):
        price = team_price(r.get("unitprice"))
        if price is not None:
            todo.append((str(r["id"]), str(r["externalid"]), price))
    todo = _capped(todo, max_items, "team price")
    for rid, ext, price in todo[:5]:
        print(f"  e.g. item {rid} ({ext}): Suggested Team Price -> {price:.2f}")
    if not allow_write:
        print(f"team price: WOULD write (dry run) {len(todo):,}")
        return 0

    sent = failures = 0
    for i in range(0, len(todo), RESTLET_BATCH):
        batch = todo[i:i + RESTLET_BATCH]
        try:
            resp = client.call_restlet(
                cfg.netsuite.matrix_script_id, cfg.netsuite.matrix_deploy_id,
                {"items": [{"externalId": ext, "teamPrice": price}
                           for _rid, ext, price in batch]},
            )
        except Exception as exc:  # noqa: BLE001
            failures += len(batch)
            print(f"  RESTLET CALL FAILED: {str(exc)[:200]}")
            continue
        for res in resp.get("results", []):
            if res.get("status") == "error":
                failures += 1
                if failures <= 10:
                    print(f"  TEAM PRICE FAILED {res.get('externalId')}: "
                          f"{str(res.get('message') or '')[:140]}")
            else:
                sent += 1
        if i == 0 and not _team_price_stuck(client, [rid for rid, _e, _p in batch]):
            # The RESTlet guards the price line, so a refused value comes back
            # as a clean "updated". Believe the catalogue, not the response.
            print("team price: STOPPED after the first batch -- NetSuite accepted the "
                  "request but stored no Suggested Team Price. Either the updated "
                  "RESTlet is not deployed yet, or this account computes price level "
                  f"{TEAM_PRICE_LEVEL} from a formula and ignores a typed value. "
                  "Nothing further was sent.")
            return max(failures, 1)
    print(f"team price: wrote {sent:,}; failures: {failures:,}")
    return failures


def _team_price_stuck(client, item_ids: list[str]) -> bool:
    rows = client.suiteql(
        "SELECT COUNT(*) AS n FROM pricing "
        f"WHERE pricelevel = {TEAM_PRICE_LEVEL} AND unitprice > 0 "
        f"AND item IN ({','.join(item_ids)})"
    )
    return int((rows[0].get("n") if rows else 0) or 0) > 0


def fix_manufacturers(client, cfg, allow_write: bool, max_items: int) -> int:
    # Imported here: only this step needs the SanMar feed (SFTP credentials).
    from sanmar_parent_create import _resolve_feed

    from sanmar_netsuite.netsuite.repository import child_external_id
    from sanmar_netsuite.sanmar.parsers import parse_styles

    feed: dict[str, tuple[str, str]] = {}
    for style in parse_styles(str(_resolve_feed(cfg))):
        for sku in style.skus:
            feed[child_external_id(sku.unique_key)] = (
                str(getattr(style, "brand", "") or "").strip(),
                str(sku.gtin or "").strip(),
            )
    jobs: list[tuple[str, dict]] = []
    not_in_feed = 0
    for r in _scan(
        client,
        "SELECT id, externalid, manufacturer, upccode FROM item",
        "externalid LIKE 'SANMAR-%' AND (manufacturer IS NULL OR upccode IS NULL)",
    ):
        hit = feed.get(str(r.get("externalid") or ""))
        if hit is None:
            not_in_feed += 1
            continue
        brand, gtin = hit
        body: dict[str, object] = {}
        if brand and not str(r.get("manufacturer") or "").strip():
            body["manufacturer"] = brand
        if gtin and not str(r.get("upccode") or "").strip():
            body["upcCode"] = gtin
        if body:
            jobs.append((str(r["id"]), body))
    if not_in_feed:
        print(f"  {not_in_feed:,} item(s) are no longer in the SanMar feed -- left as is")
    return _patch(client, _capped(jobs, max_items, "manufacturer / UPC"), allow_write,
                  "manufacturer / UPC")


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    steps = [s.strip().lower() for s in
             (os.environ.get("FORMAT_FIX_STEPS") or ",".join(DEFAULT_STEPS)).split(",")
             if s.strip()]
    unknown = [s for s in steps if s not in ALL_STEPS]
    if unknown:
        print(f"unknown step(s) {unknown}; valid: {', '.join(ALL_STEPS)}")
        return 1
    client = NetSuiteClient(cfg.netsuite)
    runners: dict[str, Callable[[], int]] = {
        "displayname": lambda: fix_display_names(client, allow_write, max_items),
        "template": lambda: fix_templates(client, allow_write, max_items),
        "vendorprice": lambda: fix_vendor_prices(client, allow_write, max_items),
        "teamprice": lambda: fix_team_prices(client, cfg, allow_write, max_items),
        "manufacturer": lambda: fix_manufacturers(client, cfg, allow_write, max_items),
    }
    failures = 0
    for step in steps:
        print(f"\n--- {step} ---")
        failures += runners[step]()
    return exit_code("item format fix", failures, failures, max(failures, 1))


if __name__ == "__main__":
    raise SystemExit(main())
