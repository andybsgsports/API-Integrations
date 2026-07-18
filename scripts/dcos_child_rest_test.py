"""Test whether the REST record API can create a matrix CHILD directly.

If it can, the matrix RESTlet (and its two repo secrets) is unnecessary and the
auto-create pilot unblocks with zero NetSuite deployment. The codebase assumed
REST can't do this, but that predates proving REST creates the matrix PARENT
(220788 / style 111000). This creates one child under that parent and reports
exactly what happens -- trying a few body shapes until one sticks.

Live create gated by SYNC_DRY_RUN=false. Uses the existing parent 220788.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import COLOR_LIST, SIZE_LIST
from sanmar_netsuite.netsuite.client import NetSuiteClient

PARENT_ID = os.environ.get("TEST_PARENT_ID", "220788")   # style 111000
STYLE = os.environ.get("TEST_STYLE", "111000")
COLOR = os.environ.get("TEST_COLOR", "Black")
SIZE = os.environ.get("TEST_SIZE", "Small")


def option_id(client: NetSuiteClient, list_type: str, name: str) -> str | None:
    rows = client.suiteql(
        f"SELECT id FROM {list_type} WHERE LOWER(name) = LOWER('{name}') "
        "AND (isinactive = 'F' OR isinactive IS NULL) ORDER BY id"
    )
    return str(rows[0]["id"]) if rows else None


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    client = NetSuiteClient(cfg.netsuite)

    color_id = option_id(client, COLOR_LIST, COLOR)
    size_id = option_id(client, SIZE_LIST, SIZE)
    print(f"parent={PARENT_ID} style={STYLE} color={COLOR!r}(id {color_id}) "
          f"size={SIZE!r}(id {size_id})")
    if not color_id or not size_id:
        print("could not resolve color/size option ids -- aborting")
        return 1

    itemid = f"{STYLE}-{COLOR}-{SIZE}"
    # candidate body shapes for a matrix child via REST, most-likely first
    shapes = [
        ("matrixType+parent+option-refs", {
            "itemId": itemid,
            "matrixType": "_child",
            "parent": {"id": PARENT_ID},
            "custitem_bsg_color": {"id": color_id},
            "custitem_bsg_size": {"id": size_id},
        }),
        ("isMatrixItem parent option-refs", {
            "itemId": itemid,
            "parent": {"id": PARENT_ID},
            "custitem_bsg_color": {"id": color_id},
            "custitem_bsg_size": {"id": size_id},
        }),
        ("matrixType CHILD enum", {
            "itemId": itemid,
            "matrixType": "CHILD",
            "parent": {"id": PARENT_ID},
            "custitem_bsg_color": {"id": color_id},
            "custitem_bsg_size": {"id": size_id},
        }),
    ]

    if not allow_write:
        print("DRY RUN -- would attempt these bodies:")
        for name, body in shapes:
            print(f"  [{name}] {body}")
        return 0

    for name, body in shapes:
        print(f"\n--- attempt: {name} ---")
        try:
            new_id = client.create_record("inventoryItem", body)
            print(f"  SUCCESS: created child id {new_id or '(no location header)'}")
            print("  >>> REST CAN create matrix children -- RESTlet NOT needed.")
            return 0
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "payload", "")
            print(f"  failed: {str(exc)[:120]} :: {str(detail)[:400]}")

    print("\nAll shapes failed -- REST cannot create matrix children here; "
          "the RESTlet deployment is required.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
