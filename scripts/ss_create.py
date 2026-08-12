"""Create the S&S items NetSuite is missing -- parents, then children.

The S&S create phase, run after ``ss_create_preview.py`` (discover) and the
option pre-pass. Two paths, both driven by the preview's output:

* **adopt** -- the style already has a matrix parent (usually one SanMar
  built; 395 of 5,463 styles today). Children post straight through the
  matrix RESTlet, which resolves the parent by NAME. Nothing is done to the
  parent: grid probe run 31437315008 established that a parent does not
  restrict its children's option values -- style 112 carries 115 children
  across 112 distinct colour ids, and the 300 parents created on 2026-08-10
  were written with no grid at all and took 4,102 children.
* **create** -- no parent exists, so one is written first (full field spec:
  names keep the style code, descriptions drop it, marketing copy goes to
  Store Description, UOM/accounts/subsidiary from live references), then its
  children.

Every child carries its ``custitem_ss_*`` data at birth via the RESTlet's
vendor-neutral ``fields`` map, so an S&S item is complete the moment it
exists rather than waiting for the next back-fill.

Scope is the brand allowlist from ``ss_create_preview`` -- ``CARRIED`` plus
the Momentec-direct exclusions -- so this can never create a brand BSG buys
elsewhere. ``CREATE_MAX_STYLES`` caps net-new parents per run (the ramp).

Honours ``SYNC_DRY_RUN``: a dry run reports exactly what it would create.
"""

from __future__ import annotations

import csv
import html as _html
import json
import os
import re
from pathlib import Path

from native_pricing import base_price, weight_display
from pricing_ownership import VENDOR_SS
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
    _uom_ids,
    resolve_parent_refs,
)
from ss_backfill import effective_cost
from ss_create_preview import (
    brand_allowed,
    brand_allowlist,
    excluded_brands,
    load_products,
)

from sanmar_netsuite.config import get_config as ns_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import (
    COLOR_LIST,
    SIZE_LIST,
    MatrixOptionResolver,
)
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.csv_export import class_for_category
from sanmar_netsuite.transform.sizes import normalize_size
from ss_activewear_netsuite.config import get_config as ss_config

ROOT = Path(__file__).resolve().parents[1]
RESTLET_BATCH = 25
SS_CDN = "https://cdn.ssactivewear.com/"


def load_styles() -> dict[str, dict]:
    """style_name -> style record (title/description), from styles.json.

    /Products carries no style title, so without this a created parent has
    no Display Name or Store Description -- the fields Andy's spec makes
    mandatory at creation.
    """
    path = Path(ss_config().download_dir) / "styles.json"
    if not path.exists():
        print(f"  ({path} missing -- parents will fall back to the style code)")
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(r.get("style_name") or "").strip(): r for r in rows
            if str(r.get("style_name") or "").strip()}


def image_url(path: str | None) -> str | None:
    """S&S image paths are CDN-relative; URL fields reject anything else."""
    v = (path or "").strip()
    if not v:
        return None
    return v if v.startswith(("http://", "https://")) else SS_CDN + v.lstrip("/")


_TAG = re.compile(r"<[^>]+>")


def clean_html(text: str | None) -> str:
    """S&S style descriptions arrive as raw HTML (``<ul><li><span style=...``);
    written straight to Store Description they render as markup soup (Andy,
    2026-08-12: "looks a little funky"). Bullets become "- " lines, every
    other tag is stripped, entities unescaped.
    """
    t = _html.unescape(text or "")
    t = re.sub(r"(?i)<li[^>]*>", "\n- ", t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|ul|ol|div|li)>", "\n", t)
    t = _TAG.sub("", t)
    return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())


def display_name(style_name: str, title: str) -> str:
    """Name fields keep the style code; description fields drop it.

    A style with no title in the feed gets the bare code -- never the code
    twice, which is what naive concatenation produces.
    """
    base = (title or "").strip()
    return f"{base} {style_name}".strip() if base else style_name


def child_fields(p: dict) -> dict[str, object]:
    """The ``custitem_ss_*`` data a child is born with."""
    out = {
        "custitem_ss_sku": p.get("sku"),
        "custitem_ss_style_id": p.get("style_id"),
        "custitem_ss_style": p.get("style_name"),
        "custitem_ss_color_name": p.get("color_name"),
        "custitem_ss_color_code": p.get("color_code"),
        "custitem_ss_size_name": p.get("size_name"),
        "custitem_ss_gtin": p.get("gtin"),
        "custitem_ss_brand": p.get("brand_name"),
        "custitem_ss_case_size": p.get("case_size"),
        "custitem_ss_is_closeout": p.get("is_closeout"),
        "custitem_ss_is_discontinued": p.get("is_discontinued"),
        "custitem_ss_front_image_url": image_url(p.get("front_image_url")),
        "custitem_ss_on_model_image_url": image_url(p.get("on_model_image_url")),
    }
    for key, field in (("msrp", "custitem_ss_msrp"), ("map_price", "custitem_ss_map"),
                       ("piece_price", "custitem_ss_piece_price"),
                       ("dozen_price", "custitem_ss_dozen_price"),
                       ("case_price", "custitem_ss_case_price"),
                       ("weight", "custitem_ss_weight")):
        if p.get(key) not in (None, ""):
            out[field] = p[key]
    return {k: v for k, v in out.items() if v not in (None, "")}


def _num(p: dict, key: str) -> float | None:
    try:
        v = p.get(key)
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def ensure_options(resolver: MatrixOptionResolver, prods: list[dict]) -> None:
    """Make sure every colour/size these SKUs reference exists in its list.

    ``canonical_name`` only TRANSLATES to an existing spelling -- it never
    creates. Pilot attempt 2 (run 31536301381) lost style 0005's children to
    "color 'Dark Navy/ Smoke' not in customlist_bsg_matrix_color" because
    nothing had created the genuinely-new colours first (SanMar has a whole
    ensure-values phase for this; S&S does it inline here). ``resolve`` also
    reuses punctuation variants instead of duplicating them.
    """
    for color in {str(p.get("color_name") or "").strip() for p in prods}:
        if color:
            _id, status = resolver.resolve(COLOR_LIST, color)
            if status == "created":
                print(f"  colour created: {color!r}")
    for size in {normalize_size(str(p.get("size_name") or "")) for p in prods}:
        if size:
            _id, status = resolver.resolve(SIZE_LIST, size)
            if status == "created":
                print(f"  size created: {size!r}")


def child_payload(p: dict, style_name: str, resolver: MatrixOptionResolver,
                  uom: dict[str, str], title: str = "") -> dict[str, object]:
    """One RESTlet child payload.

    Colour and size are sent as the LIST's spelling (the RESTlet resolves
    options by exact name), never the feed's -- the lesson of the
    'Khaki/ Coffee' rejections and the 322 retired-value deaths.
    """
    color = resolver.canonical_name(COLOR_LIST, str(p.get("color_name") or "").strip())
    size = resolver.canonical_name(
        SIZE_LIST, normalize_size(str(p.get("size_name") or "")))
    regular = _num(p, "customer_price")
    if regular is None:
        regular = _num(p, "piece_price")
    cost, _on_sale = effective_cost(regular, _num(p, "sale_price"))
    weight, weight_unit = weight_display(_num(p, "weight"))
    payload: dict[str, object] = {
        "externalId": f"SS-{p.get('sku')}",
        "itemId": f"{style_name}-{color}-{size}",
        "style": style_name,
        "color": color,
        "size": size,
        "vendorName": style_name,
        "upc": p.get("gtin"),
        "cost": cost,
        "basePrice": base_price(_num(p, "msrp"), _num(p, "map_price")),
        "weight": weight,
        "preferredVendorId": str(VENDOR_SS),
        "costingMethod": "AVG",
        "displayName": display_name(style_name, title)[:60],
        "description": title or style_name,
        # Same required references SanMar's children carry, as NAMES -- the
        # RESTlet resolves them itself. Without the tax schedule NetSuite
        # rejects every child with "Please enter value(s) for: Tax Schedule"
        # (pilot attempt 2, run 31536301381, all 26 chef-coat children).
        # NO "class": the RESTlet's class-path search crashes this account.
        "incomeAccount": DEFAULT_INCOME_ACCOUNT,
        "cogsAccount": DEFAULT_COGS_ACCOUNT,
        "assetAccount": DEFAULT_ASSET_ACCOUNT,
        "taxSchedule": DEFAULT_TAX_SCHEDULE,
        "subsidiary": DEFAULT_SUBSIDIARY,
        "department": DEFAULT_DEPARTMENT,
        "location": DEFAULT_LOCATION,
        "preferredLocation": DEFAULT_LOCATION,
        "fields": child_fields(p),
    }
    if weight_unit:
        payload["weightUnitId"] = weight_unit.get("id") if isinstance(
            weight_unit, dict) else weight_unit
    for key, val in (("unitsTypeId", uom.get("unitsTypeId")),
                     ("stockUnitId", uom.get("stockUnitId")),
                     ("purchaseUnitId", uom.get("purchaseUnitId")),
                     ("saleUnitId", uom.get("saleUnitId"))):
        if val:
            payload[key] = val
    return {k: v for k, v in payload.items() if v not in (None, "")}


def _refresh_adopted_parent(client: NetSuiteClient, style_name: str,
                            meta: dict, prods: list[dict]) -> None:
    """Heal a parent WE created bare: fill blanks, de-funk our own HTML.

    Fill-blanks-only for class and the store names, so a parent another
    vendor built is never overwritten. Store Description is replaced only
    when it is empty OR byte-identical to the RAW S&S html -- i.e. provably
    ours from before clean_html existed (the pilot parents Andy reviewed).
    """
    rows = client.suiteql(
        "SELECT id, class, storedescription, storedisplayname "
        "FROM item WHERE matrixtype = 'PARENT' AND "
        f"LOWER(itemid) = LOWER('{_sql_escape(style_name)}')"
    )
    if not rows:
        return
    row = rows[0]
    raw_desc = str(meta.get("description") or "").strip()
    title = str(meta.get("title") or "").strip()
    patch: dict[str, object] = {}
    current_desc = str(row.get("storedescription") or "").strip()
    if raw_desc and current_desc in ("", raw_desc):
        cleaned = clean_html(raw_desc)
        if cleaned != current_desc:
            patch["storeDescription"] = cleaned
    if not str(row.get("class") or "").strip():
        cls_path = class_for_category(
            str(meta.get("category_name")
                or (prods[0].get("category_name") if prods else "") or ""))
        cls_id = _class_id(client, cls_path) if cls_path else None
        if cls_id:
            patch["class"] = {"id": cls_id}
    if title and not str(row.get("storedisplayname") or "").strip():
        patch["storeDisplayName"] = display_name(style_name, title)
    if patch:
        try:
            client.update_record("inventoryItem", str(row["id"]), patch)
            print(f"  parent refreshed: {sorted(patch)}")
        except Exception as exc:  # noqa: BLE001 - refresh is best-effort
            print(f"  parent refresh failed: {str(exc)[:120]}")


def post_children(client: NetSuiteClient, cfg,
                  payloads: list[dict]) -> tuple[int, int, int]:
    """Post child payloads through the matrix RESTlet in batches.

    Returns ``(created, already_present, failures)``; an "already exists"
    combo is idempotency, not a failure (see sanmar_parent_create).
    """
    created = already = failures = 0
    for i in range(0, len(payloads), RESTLET_BATCH):
        batch = payloads[i:i + RESTLET_BATCH]
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
            if r.get("status") == "error":
                msg = str(r.get("message") or "")
                if "combination of options already exists" in msg.lower():
                    already += 1
                    continue
                failures += 1
                print(f"  CHILD FAILED {r.get('externalId')}: {msg[:140]}")
            else:
                created += 1
    return created, already, failures


def main() -> int:  # noqa: PLR0912, PLR0915 - mirrors sanmar_parent_create's shape
    cfg = ns_config()
    allow_write = not cfg.sync.dry_run
    max_styles = int(os.environ.get("CREATE_MAX_STYLES", "0") or "0")

    products = load_products()
    styles_meta = load_styles()
    by_style: dict[str, list[dict]] = {}
    for p in products:
        name = str(p.get("style_name") or "").strip()
        if name:
            by_style.setdefault(name, []).append(p)

    parents_file = ROOT / "data" / "ss_new_parents.txt"
    if not parents_file.exists():
        print("data/ss_new_parents.txt missing -- run the discover phase first")
        return 1
    net_new = [ln.strip() for ln in
               parents_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    print(f"{len(net_new)} net-new style(s) from discover; "
          f"{len(by_style)} style(s) in the feed")

    client = NetSuiteClient(cfg.netsuite)
    resolver = MatrixOptionResolver(client=client, allow_create=allow_write)

    # The same brand rules the preview reported under, recomputed here so a
    # create run can never widen its own scope: CARRIED needs the carried
    # tally, which only the preview has, so creation reads the allowlist from
    # the env exactly as configured and refuses to run wide open.
    excluded = excluded_brands()
    allowed = brand_allowlist()
    if not allowed and os.environ.get("SS_CREATE_BRANDS", "").strip().upper() == "CARRIED":
        print("SS_CREATE_BRANDS=CARRIED needs the discover phase's tally; "
              "creation reads data/ss_new_parents.txt, which discover already "
              "filtered -- proceeding with the exclusion list only")
    print(f"brand scope: {len(allowed) or 'all'} allowed, {len(excluded)} excluded")

    created_parents = created_children = skipped = failures = done = 0
    parent_refs: dict[str, dict] | None = None
    uom: dict[str, str] = {}
    if allow_write:
        # Children need UOM ids too; resolve once up front (parent_refs stays
        # lazy -- only net-new parents need the account/subsidiary refs).
        uom = _uom_ids(client)
    already = 0

    for style_name in net_new:
        if max_styles and done >= max_styles:
            break
        skus = [p for p in by_style.get(style_name, [])
                if brand_allowed(str(p.get("brand_name") or ""), allowed, excluded)]
        if not skus:
            skipped += 1
            continue
        done += 1
        meta = styles_meta.get(style_name, {})
        title = str(meta.get("title") or "").strip()
        print(f"\n=== {style_name}: {len(skus)} child(ren) "
              f"[{skus[0].get('brand_name')}]")

        if allow_write:
            rows = client.suiteql(
                "SELECT id FROM item WHERE matrixtype = 'PARENT' AND "
                f"LOWER(itemid) = LOWER('{_sql_escape(style_name)}')"
            )
            if rows:
                already += 1
                print(f"  parent already exists (id {rows[0]['id']}) -- adopting")
            else:
                if parent_refs is None:
                    # Accounts / subsidiary / location / tax schedule: NetSuite
                    # REQUIRES these on any inventory item, and a body without
                    # them is a bare "400 Bad Request" naming no field -- the
                    # first live pilot (run 31535379676) lost all 5 parents to
                    # exactly that. These are BSG-wide defaults resolved from
                    # live records, not SanMar-specific values.
                    parent_refs = resolve_parent_refs(client)
                body = {
                    "itemId": style_name, "vendorName": style_name,
                    "matrixType": "PARENT", "isInactive": False, "isOnline": False,
                    # Parents carry a Preferred Vendor too (Andy, 2026-08-12:
                    # "all items would need a preferred vendor").
                    "itemVendor": {"items": [{
                        "vendor": {"id": str(VENDOR_SS)},
                        "preferredVendor": True,
                        "vendorCode": style_name,
                    }]},
                    "displayName": display_name(style_name, title)[:60],
                    "storeDisplayName": display_name(style_name, title),
                    "salesDescription": title or style_name,
                    "purchaseDescription": title or style_name,
                    "storeDescription": clean_html(str(meta.get("description") or "")),
                    **{k: {"id": v} for k, v in {
                        "unitsType": uom.get("unitsTypeId", ""),
                        "stockUnit": uom.get("stockUnitId", ""),
                        "purchaseUnit": uom.get("purchaseUnitId", ""),
                        "saleUnit": uom.get("saleUnitId", ""),
                    }.items() if v},
                    **parent_refs,
                }
                # Class, like SanMar's parents (Andy, 2026-08-12: "the sanmar
                # items have classes being filled in, but not the S&S"). The
                # keyword mapper is category-vocabulary agnostic; an unmapped
                # category leaves the parent classless and says so.
                cls_path = class_for_category(
                    str(meta.get("category_name")
                        or skus[0].get("category_name") or ""))
                cls_id = _class_id(client, cls_path) if cls_path else None
                if cls_id:
                    body["class"] = {"id": cls_id}
                elif not cls_path:
                    print("  (category unmapped -- parent gets no class)")
                try:
                    pid = client.create_record("inventoryItem", body)
                    created_parents += 1
                    print(f"  parent created: id {pid or '?'}")
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"  PARENT FAILED {style_name}: {str(exc)[:150]} "
                          f":: {str(getattr(exc, 'detail', ''))[:300]}")
                    continue
        else:
            created_parents += 1  # the dry-run tally must match its WOULD lines
            print(f"  WOULD create parent {style_name!r} "
                  f"({display_name(style_name, title)[:60]!r})")

        if allow_write:
            ensure_options(resolver, skus)
        payloads = [child_payload(p, style_name, resolver, uom, title) for p in skus]
        if not allow_write:
            created_children += len(payloads)
            print(f"  WOULD post {len(payloads)} child(ren), e.g. "
                  f"{payloads[0].get('itemId')!r}")
            continue
        c, a, f = post_children(client, cfg, payloads)
        created_children += c
        already += a
        failures += f

    # -- adopt bucket: new colours/sizes under parents that already exist ----
    # These styles never appear in ss_new_parents.txt (their parent is real,
    # usually SanMar's), so the loop above cannot reach them. UPDATE_MAX_ITEMS
    # caps how many children this pass posts (the pilot ran with 80).
    adopt_children = adopt_already = 0
    max_children = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    refresh_parents = (os.environ.get("CREATE_REFRESH_STYLES", "")
                       .strip().lower() in ("1", "true", "yes"))
    adopt_csv = ROOT / "data" / "ss_new_children.csv"
    if adopt_csv.exists():
        want: dict[str, set[str]] = {}
        with adopt_csv.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                sku = str(row.get("sku") or "").strip()
                if sku:
                    want.setdefault(str(row.get("style") or "").strip(), set()).add(sku)
        posted = 0
        for style_name in sorted(want):
            if max_children and posted >= max_children:
                print(f"  (adopt pass capped at {max_children} children)")
                break
            prods = [p for p in by_style.get(style_name, [])
                     if str(p.get("sku") or "") in want[style_name]]
            if not prods:
                continue
            if max_children:
                prods = prods[:max_children - posted]
            meta = styles_meta.get(style_name, {})
            title = str(meta.get("title") or "").strip()
            print(f"\n=== adopt {style_name}: {len(prods)} new child(ren) "
                  f"under the existing parent")
            if allow_write and refresh_parents:
                _refresh_adopted_parent(client, style_name, meta, prods)
            if allow_write:
                ensure_options(resolver, prods)
            payloads = [child_payload(p, style_name, resolver, uom, title)
                        for p in prods]
            posted += len(payloads)
            if not allow_write:
                adopt_children += len(payloads)
                print(f"  WOULD post {len(payloads)} child(ren)")
                continue
            c, a, f = post_children(client, cfg, payloads)
            adopt_children += c
            adopt_already += a
            failures += f

    verb = "created" if allow_write else "WOULD create (dry run)"
    print(f"\nss create: {verb} {created_parents} parent(s), "
          f"{created_children} child(ren) under them, {adopt_children} "
          f"child(ren) under adopted parents; styles skipped (brand/no SKUs): "
          f"{skipped}; already present: {already + adopt_already}; "
          f"failures: {failures}")
    return exit_code("ss create", failures, failures,
                     created_children + adopt_children + failures)


if __name__ == "__main__":
    raise SystemExit(main())
