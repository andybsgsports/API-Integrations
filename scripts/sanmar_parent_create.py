"""Create net-new SanMar matrix items (parent + children) that NetSuite doesn't
carry at all -- the styles create-preview holds out of the child CSV because
their parent doesn't exist yet.

Structure per style, following the proven DCOS pattern
(scripts/dcos_item_create.py):
  * matrix PARENT via the REST record API (``matrixType: "PARENT"`` + accounts /
    subsidiary / location / department / tax), then
  * one matrix CHILD per SKU via the deployed BSG matrix RESTlet, which resolves
    the parent by name and the colour/size list values by name.

Parent-before-child is guaranteed: each style's parent is created (or reused if a
prior run left a childless one) before its children are submitted.

Candidates come from ``data/sanmar_new_parents.txt`` (create-preview's net-new
list). Ladder: ``SYNC_DRY_RUN`` gates every write; ``CREATE_MAX_STYLES`` caps
styles per run (pilot = 5); ``UPDATE_MAX_ITEMS`` caps children.

Colours/sizes must already exist (run ensure-values first); missing ones are
reported, not silently skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

from native_pricing import WEIGHT_UNIT_LB_ID, base_price, weight_display
from pricing_ownership import VENDOR_SANMAR
from sanmar_field_update import (
    display_name_with_style,
    store_description,
    store_display_name,
)

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import COLOR_LIST, SIZE_LIST, MatrixOptionResolver
from sanmar_netsuite.netsuite.repository import _sql_escape, child_external_id
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp
from sanmar_netsuite.transform.csv_export import (
    DEFAULT_ASSET_ACCOUNT,
    DEFAULT_COGS_ACCOUNT,
    DEFAULT_DEPARTMENT,
    DEFAULT_INCOME_ACCOUNT,
    DEFAULT_LOCATION,
    DEFAULT_SUBSIDIARY,
    DEFAULT_TAX_SCHEDULE,
    class_for_category,
)
from sanmar_netsuite.transform.sizes import normalize_size

ROOT = Path(__file__).resolve().parents[1]
RESTLET_BATCH = 25


def resolve_parent_refs(client: NetSuiteClient) -> dict[str, dict]:
    """Resolve the ids the REST parent body needs; raises on hard misses."""
    refs: dict[str, dict] = {}
    acct = {}
    for num in (DEFAULT_INCOME_ACCOUNT, DEFAULT_COGS_ACCOUNT, DEFAULT_ASSET_ACCOUNT):
        rows = client.suiteql(f"SELECT id FROM account WHERE acctnumber = '{_sql_escape(num)}'")
        if not rows:
            raise RuntimeError(f"account number {num} not found")
        acct[num] = str(rows[0]["id"])
    refs["incomeAccount"] = {"id": acct[DEFAULT_INCOME_ACCOUNT]}
    refs["cogsAccount"] = {"id": acct[DEFAULT_COGS_ACCOUNT]}
    refs["assetAccount"] = {"id": acct[DEFAULT_ASSET_ACCOUNT]}

    sub_leaf = DEFAULT_SUBSIDIARY.split(" : ")[-1]
    rows = client.suiteql(f"SELECT id FROM subsidiary WHERE name = '{_sql_escape(sub_leaf)}'")
    if rows:
        refs["subsidiary"] = {"id": str(rows[0]["id"])}
    rows = client.suiteql(f"SELECT id FROM location WHERE name = '{_sql_escape(DEFAULT_LOCATION)}'")
    if rows:
        refs["location"] = {"id": str(rows[0]["id"])}
        refs["preferredLocation"] = {"id": str(rows[0]["id"])}
    rows = client.suiteql(
        f"SELECT id FROM department WHERE name = '{_sql_escape(DEFAULT_DEPARTMENT)}'"
    )
    if rows:
        refs["department"] = {"id": str(rows[0]["id"])}
    # Tax schedule: the taxschedule table isn't query-exposed in every account;
    # borrow the ref an existing inventory item already carries.
    try:
        rows = client.suiteql(
            "SELECT id FROM item WHERE itemtype = 'InvtPart' AND isinactive = 'F' AND rownum <= 1"
        )
        rec = client.get_record("inventoryItem", str(rows[0]["id"]))
        ts = (rec.get("taxSchedule") or {}).get("id")
        if ts:
            refs["taxSchedule"] = {"id": str(ts)}
    except Exception as exc:  # noqa: BLE001
        print(f"  taxSchedule lookup failed ({str(exc)[:80]})")
    if "taxSchedule" not in refs:
        raise RuntimeError("could not resolve a Tax Schedule id (parents need one)")
    return refs


def _resolve_feed(config) -> Path:
    local = Path(config.sftp.download_dir) / C.FILE_SDL_N
    return local if local.exists() else SanMarSftp(config.sftp).download(C.FILE_SDL_N)


_CLASS_IDS: dict[str, str | None] = {}


def _class_id(client: NetSuiteClient, path: str) -> str | None:
    """Internal id for a NetSuite Class path like 'Tops : Sweatshirts'.

    SuiteQL's classification.fullname carries the whole path, so no
    parent-filtered search is needed (same approach as
    sanmar_department_class_fix.resolve_class_ids). Unmapped/unknown paths
    resolve to None and the parent is simply created without a class.
    """
    if not path:
        return None
    if path not in _CLASS_IDS:
        rows = client.suiteql(
            f"SELECT id FROM classification WHERE fullname = '{_sql_escape(path)}'"
        )
        _CLASS_IDS[path] = str(rows[0]["id"]) if rows else None
        if not rows:
            print(f"  (class path {path!r} not found in NetSuite -- parent gets no class)")
    return _CLASS_IDS[path]


def _uom_ids(client: NetSuiteClient) -> dict[str, str]:
    """Units-of-measure internal ids for new items, copied from a live
    reference item (item_uom_fix.py's proven approach -- never guessed).

    Returns restlet payload keys -> ids: unitsTypeId / stockUnitId /
    purchaseUnitId / saleUnitId. Empty when no reference resolves; creation
    proceeds without units and item_uom_fix backfills.
    """
    ref_id = os.environ.get("UOM_REFERENCE_ID", "").strip()
    try:
        if not ref_id:
            rows = client.suiteql(
                "SELECT id FROM item WHERE matrixtype IN ('PARENT', 'CHILD') "
                "AND unitstype IS NOT NULL AND rownum <= 1"
            )
            if not rows:
                print("  (no UOM reference item found -- units left for item_uom_fix)")
                return {}
            ref_id = str(rows[0]["id"])
        ref = client.get_record("inventoryItem", ref_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  (UOM reference lookup failed: {str(exc)[:80]} -- units left blank)")
        return {}
    out: dict[str, str] = {}
    for payload_key, field in (
        ("unitsTypeId", "unitsType"), ("stockUnitId", "stockUnit"),
        ("purchaseUnitId", "purchaseUnit"), ("saleUnitId", "saleUnit"),
    ):
        v = ref.get(field)
        if isinstance(v, dict) and v.get("id") is not None:
            out[payload_key] = str(v["id"])
    print(f"  UOM ids from reference item {ref_id}: {out}")
    return out


def _child_payload(style, sku, resolver=None, uom=None) -> dict:
    # Colour/size go to the RESTlet as NAMES it resolves by exact match, so a
    # punctuation variant of an existing list value must be sent as the LIST's
    # spelling -- 'Khaki/ Coffee' was rejected with "not in
    # customlist_bsg_matrix_color" while 'Khaki/Coffee' sat on the list
    # (live pilot retry, run 31036109523).
    color = sku.color_name
    size = normalize_size(sku.size)
    if resolver is not None:
        color = resolver.canonical_name(COLOR_LIST, color)
        size = resolver.canonical_name(SIZE_LIST, size)
    # Native pricing at BIRTH must match the rules the nightly update phase
    # applies, or every created item is immediately wrong and waits for a
    # correction pass. Two rules were out of step here:
    #   * Base Price is the higher of MAP and MSRP -- not MSRP alone, which
    #     under-priced every no-MSRP / MAP-only SKU.
    #   * Cost is the CASE price (SanMar's by-the-case unit price), falling
    #     back to the single-piece price -- piece price runs ~$1 higher and is
    #     exactly what made Purchase Price read too high (see the note in
    #     sanmar_field_update.build_payloads).
    # Sale-aware cost and the On Sale flag need the dip feed's sale windows,
    # which this phase doesn't load; the update phase runs minutes later in the
    # same vendor pipeline and applies them.
    regular_cost = sku.case_price if sku.case_price is not None else sku.piece_price
    weight_lb, _weight_unit = weight_display(
        None if sku.piece_weight is None else float(sku.piece_weight)
    )
    # Andy's spec (2026-08-05 pilot review): Display Name keeps the style code
    # ("Richardson Printed Five-Panel Trucker 112PFP"); Sales/Purchase
    # Description carry the same title WITHOUT the code. The long marketing
    # copy goes to the PARENT's Store Description only (children silently
    # discard store fields, proven live 2026-07-29).
    clean_title = store_display_name(style.title, style.style)
    named_title = display_name_with_style(style.title, style.style)
    # Per-colour feed images (Andy, 2026-08-05: included at creation).
    # primary_url() is the FRONT view -> the storefront's searchable image
    # column; back_url() -> custitem_sanmar_front_image_url, which despite its
    # name holds the BACK view (the true front lives on the atlas Image field,
    # populated by the atlas back-fill from the File Cabinet).
    images = style.images_by_color.get(sku.color_name)
    return {
        "externalId": child_external_id(sku.unique_key),
        "itemId": f"{style.style}-{color}-{size}",
        "style": style.style,
        "color": color,
        "size": size,
        "vendorName": style.style,
        "upc": sku.gtin,
        "cost": None if regular_cost is None else float(regular_cost),
        "basePrice": base_price(
            None if sku.msrp is None else float(sku.msrp),
            None if sku.map_price is None else float(sku.map_price),
        ),
        "weight": weight_lb,
        # Weight unit rides with the number and is always pounds -- one
        # consistent unit across the catalogue (see native_pricing).
        "weightUnitId": None if weight_lb is None else WEIGHT_UNIT_LB_ID,
        # Seed the Vendors sublist with SanMar as Preferred -- pricing
        # ownership reads this flag, and an item born without it has no
        # pricing owner until vendor_sublist.py runs.
        "preferredVendorId": str(VENDOR_SANMAR),
        # Every view the feed carries (Andy, 2026-08-05: multiple images) --
        # front to the storefront column, back plus the flats and swatch to
        # their own URL fields. A side view only exists in SanMar's web
        # service, not the file feed.
        "shopImageUrl": images.primary_url() if images else None,
        "backImageUrl": images.back_url() if images else None,
        "frontFlatUrl": images.front_flat_url if images else None,
        "backFlatUrl": images.back_flat_url if images else None,
        "swatchUrl": images.color_swatch_url if images else None,
        **(uom or {}),
        "displayName": named_title[:60],
        "description": clean_title,
        "incomeAccount": DEFAULT_INCOME_ACCOUNT,
        "cogsAccount": DEFAULT_COGS_ACCOUNT,
        "assetAccount": DEFAULT_ASSET_ACCOUNT,
        "taxSchedule": DEFAULT_TAX_SCHEDULE,
        "subsidiary": DEFAULT_SUBSIDIARY,
        "department": DEFAULT_DEPARTMENT,
        # NO "class" here, deliberately. The RESTlet resolves a "Parent :
        # Child" class path with a classification search filtered on
        # ["parent", "anyof", ...], which this account rejects ("An
        # nlobjSearchFilter contains invalid search criteria: parent."), and
        # one bad field kills the whole child create -- that is exactly how
        # 112FPC/112PFP ended up as childless parents in the live pilot (runs
        # 30954100322 / 31036109523; the three styles that DID create children
        # all had unmapped categories, so class was "" and the search never
        # ran). Class is set on the PARENT instead (resolved to an internal id
        # by SuiteQL fullname -- no RESTlet search involved), and
        # sanmar_child_finalize.py copies it down to children afterwards,
        # which is that script's whole job.
        "location": DEFAULT_LOCATION,
        "preferredLocation": DEFAULT_LOCATION,
        "costingMethod": "AVG",
    }


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_styles = int(os.environ.get("CREATE_MAX_STYLES", "0") or "0")
    max_children = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    refresh_existing = (
        os.environ.get("CREATE_REFRESH_STYLES", "").strip().lower()
        in ("1", "true", "yes")
    )
    if refresh_existing:
        print("CREATE_REFRESH_STYLES on: existing children will be re-posted "
              "(RESTlet update path), not skipped")

    candidates_file = ROOT / "data" / "sanmar_new_parents.txt"
    if not candidates_file.exists():
        print("data/sanmar_new_parents.txt missing -- run create-preview first")
        return 1
    candidates = [ln.strip() for ln in candidates_file.read_text(encoding="utf-8").splitlines()
                  if ln.strip()]
    print(f"{len(candidates)} net-new candidate style(s) from create-preview")

    feed = _resolve_feed(cfg)
    by_style = {s.style: s for s in parse_styles(str(feed))}
    client = NetSuiteClient(cfg.netsuite)
    # Create-capable option resolver: any colour/size the pilot needs but the
    # matrix lists don't carry is created inline (live) or reported (dry).
    resolver = MatrixOptionResolver(client=client, allow_create=allow_write)
    uom = _uom_ids(client)

    created_parents = created_children = skipped = failures = done = 0
    parent_refs: dict[str, dict] | None = None

    for style_name in candidates:
        if max_styles and done >= max_styles:
            break
        style = by_style.get(style_name)
        if style is None or not style.skus:
            skipped += 1
            continue
        # Defensive: skip if it already has children; reuse a childless parent.
        # CREATE_REFRESH_STYLES=true re-posts existing children instead of
        # skipping -- the RESTlet's externalId update path re-applies the
        # payload, which is how a payload-schema fix (like the 2026-08-05
        # field spec) reaches items created before it.
        safe = _sql_escape(style_name)
        existing = client.suiteql(
            f"SELECT id, itemid FROM item WHERE vendorname = '{safe}' OR itemid = '{safe}'"
        )
        if any(str(r.get("itemid") or "").strip() != style_name for r in existing):
            if not refresh_existing:
                skipped += 1
                continue
        existing_parent_id = next(
            (str(r["id"]) for r in existing
             if str(r.get("itemid") or "").strip() == style_name), None,
        )

        # Resolve (creating when live) every colour/size the children need.
        would_create = set()
        for sku in style.skus:
            _cid, cstat = resolver.resolve(COLOR_LIST, sku.color_name)
            _sid, sstat = resolver.resolve(SIZE_LIST, normalize_size(sku.size))
            if cstat in ("missing", "created"):
                would_create.add(f"colour {sku.color_name!r}")
            if sstat in ("missing", "created"):
                would_create.add(f"size {normalize_size(sku.size)!r}")
        done += 1
        print(f"\n=== {style_name}: {len(style.skus)} child(ren) ===")
        if would_create:
            verb = "created" if allow_write else "would create"
            print(f"  option value(s) {verb}: {sorted(would_create)[:8]}")

        if not allow_write:
            created_parents += 1
            created_children += len(style.skus)
            continue

        clean_title = store_display_name(style.title, style.style)
        named_title = display_name_with_style(style.title, style.style)
        if existing_parent_id:
            print(f"  parent already exists: id {existing_parent_id} (reusing)")
            if refresh_existing:
                # Bring an already-created parent up to the current field spec
                # too -- children go through the RESTlet update path, but the
                # parent body only applies at creation, which left the pilot
                # parents behind on names/store fields/class/units.
                patch: dict[str, object] = {
                    "displayName": named_title[:60],
                    "salesDescription": clean_title,
                    "purchaseDescription": clean_title,
                    "storeDisplayName": named_title,
                    "storeDescription": store_description(
                        style.available_sizes, style.description
                    ),
                }
                for rest_field, uom_key in (
                    ("unitsType", "unitsTypeId"), ("stockUnit", "stockUnitId"),
                    ("purchaseUnit", "purchaseUnitId"), ("saleUnit", "saleUnitId"),
                ):
                    if (uom or {}).get(uom_key):
                        patch[rest_field] = {"id": uom[uom_key]}
                cid = _class_id(client, class_for_category(style.category))
                if cid:
                    patch["class"] = {"id": cid}
                try:
                    client.update_record("inventoryItem", existing_parent_id, patch)
                    print(f"  parent {existing_parent_id} refreshed to current spec")
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    print(f"  PARENT REFRESH FAILED {style_name}: {str(exc)[:150]}")
        else:
            if parent_refs is None:
                parent_refs = resolve_parent_refs(client)
            body = {
                "itemId": style_name, "vendorName": style_name, "matrixType": "PARENT",
                # Active in NetSuite immediately, but NOT on the storefront
                # (Andy, 2026-07-31): nothing reaches the web store unreviewed.
                # item_web_display_fix.py turns isOnline on once an item has a
                # real image.
                "isInactive": False, "isOnline": False,
                # Andy's spec (2026-08-05): name fields keep the style code,
                # description fields drop it, and the marketing copy goes ONLY
                # to Store Description. Store fields live on the PARENT -- the
                # web-store product page -- because NetSuite silently discards
                # them on matrix children.
                "displayName": named_title[:60],
                "salesDescription": clean_title,
                "purchaseDescription": clean_title,
                "storeDisplayName": named_title,
                "storeDescription": store_description(
                    style.available_sizes, style.description
                ),
                **{k: {"id": v} for k, v in {
                    "unitsType": (uom or {}).get("unitsTypeId", ""),
                    "stockUnit": (uom or {}).get("stockUnitId", ""),
                    "purchaseUnit": (uom or {}).get("purchaseUnitId", ""),
                    "saleUnit": (uom or {}).get("saleUnitId", ""),
                }.items() if v},
                **parent_refs,
            }
            # Class lives on the PARENT (children copy it via
            # sanmar_child_finalize) -- resolved here by SuiteQL fullname, the
            # one class-path lookup this account provably supports. See the
            # note in _child_payload for why the RESTlet must not do it.
            class_id = _class_id(client, class_for_category(style.category))
            if class_id:
                body["class"] = {"id": class_id}
            try:
                pid = client.create_record("inventoryItem", body)
                created_parents += 1
                print(f"  parent created: id {pid or '?'}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  PARENT FAILED {style_name}: {str(exc)[:120]} "
                      f":: {str(getattr(exc, 'payload', ''))[:300]}")
                continue

        payloads = [_child_payload(style, sku, resolver, uom) for sku in style.skus]
        if max_children:
            payloads = payloads[: max(0, max_children - created_children)]
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
                    failures += 1
                    print(f"  CHILD FAILED {r.get('externalId')}: {r.get('message')}")
                else:
                    created_children += 1

    verb = "created" if allow_write else "WOULD create (dry run)"
    print(f"\nsanmar parent create: {verb} {created_parents} parent(s), "
          f"{created_children} child(ren); skipped: {skipped}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
