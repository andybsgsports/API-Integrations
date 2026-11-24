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
from sanmar_parent_create import (
    COLOR_LIST,
    SIZE_LIST,
    _child_payload,
    _resolve_feed,
    _uom_ids,
    normalize_size,
)

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import MatrixOptionResolver
from sanmar_netsuite.netsuite.merge import prepare_merge
from sanmar_netsuite.netsuite.repository import child_external_id
from sanmar_netsuite.sanmar.parsers import parse_styles

ROOT = Path(__file__).resolve().parents[1]
RESTLET_BATCH = 25


def is_name_taken(message: str | None) -> bool:
    """Is this RESTlet error "an item with that name already exists"?

    NetSuite refuses a child whose item name is already used by another item
    (an older hand-made item, or one under a different parent). Re-running
    cannot fix it and nothing was written, so it is a SKIP for Andy's daily
    list, not a failure that turns the whole night red (Andy, 2026-11-12:
    4 of these failed the 2026-11-12 create leg, run 43213713852).
    """
    return "uniqueness error" in (message or "").lower()


class RetiredOption(Exception):
    """The child needs a colour or size that is inactive on the BSG list."""


def is_option_not_on_list(message: str | None) -> bool:
    """RESTlet's "colour/size not on the list" rejection.

    ``ensure_options`` has already made sure the value EXISTS, so by the time
    the RESTlet says this the value is there but inactive -- a skip for
    Andy's daily list, like ``RetiredOption``.
    """
    return "not in customlist_bsg_matrix_" in (message or "").lower()


def ensure_options(resolver, sku) -> list[str]:
    """Make sure this child's colour and size are on the matrix lists.

    The parent-create step has always done this for net-new styles; children
    added under EXISTING parents skipped it, so a colour new to BSG failed
    with "color 'Athletic Grey' not in customlist_bsg_matrix_color"
    (2026-11-12, run 43213713852). Returns the values it created (or, on a
    dry run, would create). Raises if NetSuite rejects a value.
    """
    new: list[str] = []
    size = normalize_size(sku.size)
    # A value someone retired is skipped, never reactivated or re-created
    # (Andy, 2026-11-13). Six 'Athletic Grey' children hit this in the
    # sandbox, where the colour cleanup had retired that value (run
    # 43240364726).
    if resolver.is_retired(COLOR_LIST, sku.color_name):
        raise RetiredOption(f"colour {sku.color_name!r} is inactive on the BSG list")
    if resolver.is_retired(SIZE_LIST, size):
        raise RetiredOption(f"size {size!r} is inactive on the BSG list")
    _cid, cstat = resolver.resolve(COLOR_LIST, sku.color_name)
    if cstat in ("missing", "created"):
        new.append(f"colour {sku.color_name!r}")
    _sid, sstat = resolver.resolve(SIZE_LIST, size)
    if sstat in ("missing", "created"):
        new.append(f"size {size!r}")
    return new


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    if not max_items:
        # A ramp cap of its own, like CREATE_MAX_STYLES. This step's first
        # night faced the whole ~10k-child backlog the dead CSV importer had
        # stranded, and draining it at 25/RESTlet-call plus the normal
        # 300-style batch blew the create leg's 350-minute job cap (run
        # 31566421138, killed at exactly 5h50m -- GitHub labels that
        # "cancelled"). Capped, the backlog clears in ~3 nights and the leg
        # always fits its clock; steady-state nights never near the cap.
        max_items = int(os.environ.get("SANMAR_CHILD_CREATE_MAX", "4000") or "4000")

    styles = parse_styles(str(_resolve_feed(cfg)))
    client = NetSuiteClient(cfg.netsuite)
    parent_refs, skip = prepare_merge(client, styles)
    print(f"{len(parent_refs)} existing parent(s); "
          f"{len(skip)} colour/size combo(s) already present")

    resolver = MatrixOptionResolver(client=client, allow_create=allow_write)
    uom = _uom_ids(client) if allow_write else {}

    payloads: list[dict] = []
    per_style: dict[str, int] = {}
    new_options: set[str] = set()
    option_failures = retired = 0
    for style in styles:
        if style.style not in parent_refs:
            continue  # net-new parent -> sanmar_parent_create's job
        for sku in style.skus:
            if child_external_id(sku.unique_key) in skip:
                continue
            if max_items and len(payloads) >= max_items:
                break
            try:
                new_options.update(ensure_options(resolver, sku))
            except RetiredOption as exc:
                retired += 1
                print(f"  CHILD SKIPPED {style.style} "
                      f"[{sku.color_name} / {sku.size}]: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - one value must not kill the leg
                option_failures += 1
                print(f"  OPTION FAILED for {style.style} "
                      f"[{sku.color_name} / {sku.size}]: {str(exc)[:200]}")
                continue
            payload = _child_payload(style, sku, resolver, uom)
            # The parent this run already resolved (case-insensitively, and
            # preferring the one that has children). Sent as an id so the
            # RESTlet cannot re-resolve the bare NAME to an empty duplicate.
            payload["parentId"] = parent_refs[style.style]
            payloads.append(payload)
            per_style[style.style] = per_style.get(style.style, 0) + 1

    print(f"{len(payloads)} new child(ren) under {len(per_style)} existing "
          f"parent(s)" + (f" (capped at {max_items})" if max_items else ""))
    for name, n in sorted(per_style.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {name}: {n}")
    if new_options:
        verb = "created" if allow_write else "would create"
        print(f"  option value(s) {verb}: {sorted(new_options)}")

    if not allow_write:
        print("\nsanmar child create: DRY RUN -- nothing posted")
        return 0

    # externalId -> the item NAME we asked NetSuite for, so a rejection can
    # name the colliding item instead of only an opaque external id. The
    # 2026-09-22 run logged 227 "Uniqueness error - there is already an item
    # with that name or name/parent combination" with no way to tell WHICH
    # name collided (run 35707265204).
    name_by_ext = {str(pl.get("externalId")): str(pl.get("itemId") or "?")
                   for pl in payloads}
    created = updated = already = name_taken = 0
    failures = option_failures
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
                ext = str(r.get("externalId"))
                if is_name_taken(msg):
                    name_taken += 1
                    print(f"  CHILD SKIPPED {ext} "
                          f"[{name_by_ext.get(ext, '?')}]: an item with that "
                          f"name already exists")
                    continue
                if is_option_not_on_list(msg):
                    retired += 1
                    print(f"  CHILD SKIPPED {ext} "
                          f"[{name_by_ext.get(ext, '?')}]: colour or size is "
                          f"inactive on the BSG list ({msg[:80]})")
                    continue
                failures += 1
                print(f"  CHILD FAILED {ext} "
                      f"[{name_by_ext.get(ext, '?')}]: {msg[:140]}")
            elif r.get("status") == "updated":
                # The RESTlet found this external id already on an item and
                # only re-applied its fields. That is NOT a creation: counting
                # it as one made the log claim ~3,600 new children a night
                # while the catalogue never grew (2026-11-06 .. 11-11).
                updated += 1
            else:
                created += 1

    print(f"\nsanmar child create: created {created} child(ren); "
          f"already existed, fields re-applied: {updated}; "
          f"already present under another external id: {already}; "
          f"skipped, item name already exists: {name_taken}; "
          f"skipped, colour or size inactive: {retired}; "
          f"failures: {failures}")
    return exit_code("sanmar child create", failures, failures,
                     created + updated + failures)


if __name__ == "__main__":
    raise SystemExit(main())
