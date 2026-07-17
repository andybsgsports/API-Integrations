"""Find (or create) a File Cabinet folder for supplier item images.

The REST record API doesn't expose a 'folder' record type (folders live under
SOAP's document services), so this first tries to find a folder by name, then
falls back to any existing folder — good enough to prove the image-upload
write path works; the folder can be reorganized once the format is confirmed.
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

FOLDER_NAME = "Supplier Item Images"


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)
    rows = client.suiteql(
        f"SELECT id, name FROM mediaitemfolder WHERE name = '{FOLDER_NAME}'"
    )
    if rows:
        found_id = rows[0]["id"]
        print(f"FOUND folder '{FOLDER_NAME}' -> id {found_id}")
        return 0

    print(f"no folder named '{FOLDER_NAME}' found; listing existing folders")
    rows = client.suiteql("SELECT id, name FROM mediaitemfolder FETCH FIRST 10 ROWS ONLY")
    for r in rows:
        print(f"  id {r['id']}: {r.get('name')!r}")
    if rows:
        pick_id = rows[0]["id"]
        print(f"\nUSING folder id {pick_id} for the test")
        return 0
    print("no folders found at all")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
