"""Reactivate retired matrix colour list value(s) by name.

The colour consolidation retires duplicates by setting ``isInactive`` on the
list value, and the option resolver deliberately never hands out a retired id
(2026-08-08: 322 children died on exactly that). But when a colour exists
ONLY as retired values -- no active spelling anywhere -- new children that
need it cannot be created at all. 'ASH GREY' hit this: 6 children across the
5947xx/5948xx/594902 styles blocked, and Andy's call (2026-08-10) was to
reactivate the value rather than remap.

Guards:

* A name that also has an ACTIVE variant (same ``normalize_option_name`` key)
  is refused -- reactivating it would recreate the active-duplicate mess the
  consolidation cleaned up. That situation calls for a repoint, not this.
* Only values whose name matches the request case-insensitively are touched.

Env: ``COLOR_NAMES`` (comma-separated), ``SYNC_DRY_RUN``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.matrix_options import (
    COLOR_LIST,
    normalize_option_name,
)


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    names = [n.strip() for n in os.environ.get("COLOR_NAMES", "").split(",") if n.strip()]
    if not names:
        print("COLOR_NAMES is empty -- nothing to do")
        return 1

    client = NetSuiteClient(cfg.netsuite)
    rows = client.suiteql(f"SELECT id, name, isinactive FROM {COLOR_LIST} ORDER BY id")

    verb = "reactivated" if allow_write else "would reactivate"
    flipped = failures = 0
    for wanted in names:
        matches = [r for r in rows
                   if str(r.get("name") or "").casefold() == wanted.casefold()]
        if not matches:
            print(f"'{wanted}': no list value with that name -- skipped")
            failures += 1
            continue
        retired = [r for r in matches if str(r.get("isinactive") or "F") == "T"]
        if not retired:
            print(f"'{wanted}': already active (id "
                  f"{', '.join(str(r['id']) for r in matches)}) -- nothing to do")
            continue
        # An active value under a different spelling of the same colour means
        # the consolidation repointed this name away on purpose.
        key = normalize_option_name(wanted)
        active_variants = [
            r for r in rows
            if normalize_option_name(str(r.get("name") or "")) == key
            and str(r.get("isinactive") or "F") != "T"
        ]
        if active_variants:
            print(f"'{wanted}': REFUSED -- active variant already exists "
                  f"({', '.join(repr(str(r['name'])) for r in active_variants)}); "
                  f"repoint to it instead of reactivating a duplicate")
            failures += 1
            continue
        for r in retired:
            vid = str(r["id"])
            if allow_write:
                try:
                    client.update_record(COLOR_LIST, vid, {"isInactive": False})
                except Exception as exc:  # noqa: BLE001 - report, keep going
                    print(f"'{wanted}' id {vid}: FAILED -- {str(exc)[:200]}")
                    failures += 1
                    continue
            print(f"'{wanted}' id {vid}: {verb}")
            flipped += 1

    print(f"\ncolor reactivate: {verb} {flipped} value(s); failures: {failures}")
    if not allow_write and flipped:
        print("(dry run -- set SYNC_DRY_RUN=false to write)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
