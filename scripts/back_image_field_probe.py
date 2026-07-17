"""Read-only: does a dedicated SanMar back-image URL field already exist in
NetSuite (the same way custitem_sanmar_front_image_url turned out to exist,
unused, earlier this session)? Tries a handful of plausible field ids and
reports which ones SuiteQL accepts.
"""

from __future__ import annotations

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

CANDIDATES = [
    "custitem_sanmar_back_image_url",
    "custitem_sanmar_backimageurl",
    "custitem_sanmar_back_img_url",
    "custitem_sanmar_rear_image_url",
]


def main() -> int:
    client = NetSuiteClient(get_config().netsuite)
    for field in CANDIDATES:
        try:
            rows = client.suiteql(f"SELECT id, {field} FROM item FETCH FIRST 1 ROWS ONLY")
            print(f"EXISTS: {field} (sample value: {rows[0].get(field)!r})")
        except Exception as exc:  # noqa: BLE001
            print(f"not found: {field} :: {str(exc)[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
