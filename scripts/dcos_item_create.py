"""Auto-create NetSuite items for DCOS feed products that don't exist yet (CI).

Pilot suppliers: Rip-It and Baden -- small allowlists, zero existing NetSuite
items, so every allowlisted feed style is a create candidate. Structure per
style (user-confirmed): one matrix PARENT (itemid = feed style, created via
the REST record API) plus one matrix CHILD per feed part (created via the
deployed BSG matrix RESTlet, the proven child path -- it resolves the parent
by name and the color/size list values by name).

Creation builds only the structural skeleton: names, options, accounts.
The nightly ``dcos_backfill.py`` then fills UPC / supplier fields / heartbeat
stamps on its next pass, because the new children match by vendorname+options.

Ladder: ``SYNC_DRY_RUN`` gates every write (including option-value creation);
``CREATE_MAX_STYLES`` caps styles per run (smoke = 1); ``UPDATE_MAX_ITEMS``
caps children per run.
"""

from __future__ import annotations

import os
import re

from dcos_backfill import (  # scripts/ is on sys.path when run from scripts/
    SUPPLIERS,
    get_parts,
    get_sellable_styles,
    load_allowlist,
)

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import MatrixOptionResolver
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.transform.csv_export import (
    DEFAULT_ASSET_ACCOUNT,
    DEFAULT_COGS_ACCOUNT,
    DEFAULT_DEPARTMENT,
    DEFAULT_INCOME_ACCOUNT,
    DEFAULT_LOCATION,
    DEFAULT_SUBSIDIARY,
    DEFAULT_TAX_SCHEDULE,
)
from sanmar_netsuite.transform.sizes import normalize_size

# Only these suppliers may create items (user-approved pilot).
CREATE_ENABLED = {"ripit", "baden"}
RESTLET_BATCH = 25


def clean_color(raw: str) -> str:
    c = re.sub(r"\(.*?\)", "", raw or "")
    c = re.sub(r"^(?:LEFT|RIGHT)\s+HAND:\s*", "", c, flags=re.I)
    return " ".join(c.split()).strip()


def allow_match(style: str, allow: set[str]) -> bool:
    st = style.upper()
    if st in allow:
        return True
    if re.sub(r"\d+$", "", st) in allow:  # numeric-suffix loosening (TCK-style)
        return True
    return st.split("-")[0] in allow  # dash-suffix loosening (Baden '2BSFPY-NS-00')


def resolve_parent_refs(client: NetSuiteClient) -> dict[str, dict]:
    """Resolve the ids the REST parent body needs; raises on hard misses."""
    refs: dict[str, dict] = {}
    acct = {}
    for num in (DEFAULT_INCOME_ACCOUNT, DEFAULT_COGS_ACCOUNT, DEFAULT_ASSET_ACCOUNT):
        rows = client.suiteql(
            f"SELECT id FROM account WHERE acctnumber = '{_sql_escape(num)}'"
        )
        if not rows:
            raise RuntimeError(f"account number {num} not found")
        acct[num] = str(rows[0]["id"])
    refs["incomeAccount"] = {"id": acct[DEFAULT_INCOME_ACCOUNT]}
    refs["cogsAccount"] = {"id": acct[DEFAULT_COGS_ACCOUNT]}
    refs["assetAccount"] = {"id": acct[DEFAULT_ASSET_ACCOUNT]}

    sub_leaf = DEFAULT_SUBSIDIARY.split(" : ")[-1]
    rows = client.suiteql(
        f"SELECT id FROM subsidiary WHERE name = '{_sql_escape(sub_leaf)}'"
    )
    if rows:
        refs["subsidiary"] = {"id": str(rows[0]["id"])}
    rows = client.suiteql(
        f"SELECT id FROM location WHERE name = '{_sql_escape(DEFAULT_LOCATION)}'"
    )
    if rows:
        refs["location"] = {"id": str(rows[0]["id"])}
    rows = client.suiteql(
        f"SELECT id FROM department WHERE name = '{_sql_escape(DEFAULT_DEPARTMENT)}'"
    )
    if rows:
        refs["department"] = {"id": str(rows[0]["id"])}
    try:
        rows = client.suiteql(
            f"SELECT id FROM taxschedule WHERE name = '{_sql_escape(DEFAULT_TAX_SCHEDULE)}'"
        )
        if rows:
            refs["taxSchedule"] = {"id": str(rows[0]["id"])}
    except Exception:  # noqa: BLE001 - table not query-exposed in every account
        pass
    return refs


def main() -> int:
    key = (os.environ.get("DCOS_SUPPLIER") or "").strip().lower()
    if key not in CREATE_ENABLED:
        print(f"supplier {key!r} is not create-enabled (pilot: {sorted(CREATE_ENABLED)})")
        return 1
    sup = SUPPLIERS[key]
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_children = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    max_styles = int(os.environ.get("CREATE_MAX_STYLES", "0") or "0")
    key_id = os.environ.get("DCOS_KEY_ID", "")
    key_pw = os.environ.get("DCOS_KEY_PASSWORD", "")
    if not key_id or not key_pw:
        print("DCOS_KEY_ID / DCOS_KEY_PASSWORD not set")
        return 1

    allow = load_allowlist(key)
    if not allow:
        print(f"no allowlist for {key}; refusing to create from an unfiltered feed")
        return 1

    base = f"https://api.dc-onesource.com/xml/{sup['slug']}"
    feed_styles = get_sellable_styles(base, key_id, key_pw)
    candidates = sorted({s for s in feed_styles if allow_match(s, allow)})
    print(f"{sup['label']}: {len(feed_styles):,} feed styles; "
          f"{len(candidates)} on the price list")

    client = NetSuiteClient(cfg.netsuite)
    resolver = MatrixOptionResolver(client=client, allow_create=allow_write)

    created_parents = created_children = skipped_styles = failures = 0
    styles_done = 0
    parent_refs: dict[str, dict] | None = None
    for style in candidates:
        if max_styles and styles_done >= max_styles:
            break
        safe = _sql_escape(style)
        existing = client.suiteql(
            f"SELECT id FROM item WHERE vendorname = '{safe}' OR itemid = '{safe}'"
        )
        if existing:
            skipped_styles += 1
            continue

        try:
            parts = get_parts(base, key_id, key_pw, style)
        except Exception as exc:  # noqa: BLE001
            print(f"  {style}: getProduct failed ({str(exc)[:80]}); skipping")
            skipped_styles += 1
            continue
        children = []
        for part in parts:
            color = clean_color(part["colors"][0]) if part.get("colors") else ""
            size = normalize_size(part["sizes"][0]) if part.get("sizes") else ""
            if not color or not size:
                continue
            children.append((part, color, size))
        if not children:
            print(f"  {style}: no parts with both color and size; skipping "
                  f"({len(parts)} part(s) in feed)")
            skipped_styles += 1
            continue

        # option values (created live, reported in dry run)
        missing_opts = []
        for _p, color, size in children:
            cid, cstat = resolver.resolve("customlist_bsg_matrix_color", color)
            sid, sstat = resolver.resolve("customlist_bsg_matrix_size", size)
            if cstat == "missing":
                missing_opts.append(f"color {color!r}")
            if sstat == "missing":
                missing_opts.append(f"size {size!r}")

        styles_done += 1
        print(f"\n=== {style}: {len(children)} child(ren) "
              f"{[f'{c}-{s}' for _p, c, s in children][:6]}"
              f"{' ...' if len(children) > 6 else ''}")
        if missing_opts:
            print(f"  would create option value(s): {sorted(set(missing_opts))}")

        if not allow_write:
            created_parents += 1
            created_children += len(children)
            continue

        if parent_refs is None:
            parent_refs = resolve_parent_refs(client)
        parent_body = {
            "itemId": style,
            "vendorName": style,
            "matrixType": "PARENT",
            "isInactive": False,
            **parent_refs,
        }
        try:
            pid = client.create_record("inventoryItem", parent_body)
            created_parents += 1
            print(f"  parent created: id {pid or '?'}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            detail = getattr(exc, "payload", "")
            print(f"  PARENT FAILED {style}: {str(exc)[:120]} :: {str(detail)[:400]}")
            continue

        payloads = []
        for part, color, size in children:
            if max_children and created_children + len(payloads) >= max_children:
                break
            payloads.append({
                "externalId": f"DCOS-{key.upper()}-{part['partId']}",
                "itemId": f"{style}-{color}-{size}",
                "style": style,
                "color": color,
                "size": size,
                "vendorName": style,
                "incomeAccount": DEFAULT_INCOME_ACCOUNT,
                "cogsAccount": DEFAULT_COGS_ACCOUNT,
                "assetAccount": DEFAULT_ASSET_ACCOUNT,
                "taxSchedule": DEFAULT_TAX_SCHEDULE,
                "subsidiary": DEFAULT_SUBSIDIARY,
                "department": DEFAULT_DEPARTMENT,
                "location": DEFAULT_LOCATION,
                "costingMethod": "AVG",
            })
        for i in range(0, len(payloads), RESTLET_BATCH):
            batch = payloads[i : i + RESTLET_BATCH]
            try:
                response = client.call_restlet(
                    cfg.netsuite.matrix_script_id,
                    cfg.netsuite.matrix_deploy_id,
                    {"items": batch},
                )
            except Exception as exc:  # noqa: BLE001
                failures += len(batch)
                print(f"  RESTLET CALL FAILED: {str(exc)[:200]}")
                continue
            for r in response.get("results", []):
                if r.get("status") == "error":
                    failures += 1
                    print(f"  CHILD FAILED {r.get('externalId')}: {r.get('message')}")
                else:
                    created_children += 1

    verb = "created" if allow_write else "WOULD create (dry run)"
    print(f"\n{key} item create: {verb} {created_parents} parent(s), "
          f"{created_children} child(ren); styles already present: {skipped_styles}; "
          f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
