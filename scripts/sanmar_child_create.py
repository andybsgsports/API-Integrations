"""Create new SanMar children under EXISTING parents -- via the matrix RESTlet.

Replaces ``sanmar_csv_import.py`` in the create phase. That path drove a saved
CSV import through the ``bsg_csv_import`` Suitelet, and on 2026-08-11 the
Suitelet deployment came back ``SSS_INVALID_SCRIPTLET_ID`` -- disabled or gone
in the sandbox -- stranding ~10k new-child rows (issue #116, run 31460132379).
Andy's call: reroute through the matrix RESTlet rather than resurrect the
Suitelet. The RESTlet is the path that already creates thousands of children a
night under net-new parents, resolves its parent by NAME (so existing parents
just work), upserts by externalId (idempotent re-runs), and carries the full
field spec -- images, UOM, preferred vendor -- which the CSV map never did.

Retires three secrets and one NetSuite-side moving part that proved it can
silently die.

Same bucketing as the preview: ``prepare_merge`` finds the existing parents
and already-present combos; only new-children-under-existing-parents are
posted here (net-new parent styles belong to ``sanmar_parent_create``).
``UPDATE_MAX_ITEMS`` caps posts per run; honours ``SYNC_DRY_RUN``.
"""

from __future__ import annotations

import os
from pathlib import Path

from run_status import exit_code
from sanmar_parent_create import _child_payload, _resolve_feed, _uom_ids

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import MatrixOptionResolver
from sanmar_netsuite.netsuite.merge import prepare_merge
from sanmar_netsuite.netsuite.repository import child_external_id
from sanmar_netsuite.sanmar.parsers import parse_styles

ROOT = Path(__file__).resolve().parents[1]
RESTLET_BATCH = 25


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")

    styles = parse_styles(str(_resolve_feed(cfg)))
    client = NetSuiteClient(cfg.netsuite)
    parent_refs, skip = prepare_merge(client, styles)
    print(f"{len(parent_refs)} existing parent(s); "
          f"{len(skip)} colour/size combo(s) already present")

    resolver = MatrixOptionResolver(client=client, allow_create=allow_write)
    uom = _uom_ids(client) if allow_write else {}

    payloads: list[dict] = []
    per_style: dict[str, int] = {}
    for style in styles:
        if style.style not in parent_refs:
            continue  # net-new parent -> sanmar_parent_create's job
        for sku in style.skus:
            if child_external_id(sku.unique_key) in skip:
                continue
            if max_items and len(payloads) >= max_items:
                break
            payloads.append(_child_payload(style, sku, resolver, uom))
            per_style[style.style] = per_style.get(style.style, 0) + 1

    print(f"{len(payloads)} new child(ren) under {len(per_style)} existing "
          f"parent(s)" + (f" (capped at {max_items})" if max_items else ""))
    for name, n in sorted(per_style.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {name}: {n}")

    if not allow_write:
        print("\nsanmar child create: DRY RUN -- nothing posted")
        return 0

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

    print(f"\nsanmar child create: created {created} child(ren); "
          f"already present under another external id: {already}; "
          f"failures: {failures}")
    return exit_code("sanmar child create", failures, failures,
                     created + failures)


if __name__ == "__main__":
    raise SystemExit(main())
