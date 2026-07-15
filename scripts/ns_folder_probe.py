"""Find (or create) a File Cabinet folder for supplier item images. Read-only
unless FOLDER_CREATE=true.
"""

from __future__ import annotations

import os

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

FOLDER_NAME = "Supplier Item Images"


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)
    rows = client.suiteql(
        f"SELECT id, name FROM mediaitemfolder WHERE name = '{FOLDER_NAME}'"
    )
    if rows:
        print(f"FOUND folder '{FOLDER_NAME}' -> id {rows[0]['id']}")
        return 0
    print(f"no folder named '{FOLDER_NAME}' found")
    if (os.environ.get("FOLDER_CREATE") or "").lower() != "true":
        print("(FOLDER_CREATE not set — not creating)")
        return 1
    try:
        new_id = client.create_record("folder", {"name": FOLDER_NAME})
        print(f"CREATED folder '{FOLDER_NAME}' -> id {new_id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        payload = getattr(exc, "payload", "")
        print(f"folder creation failed: {str(exc)[:150]} :: {str(payload)[:400]}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
