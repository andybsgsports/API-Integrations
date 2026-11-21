"""Sanmar link apply: stamp the UPC and Sanmar unique key on CLEAN matches only.

The write half of the link step (``sanmar_link_report.py`` is the read-only
half). An item is linked only when the report would call it clean:

* exactly one NetSuite item fits the feed SKU, and one feed SKU fits the item;
* the item has a Sanmar vendor line (the check must be available -- if vendor
  lines cannot be read, nothing is linked);
* the item is active and not named INACTIVE;
* the item has no UPC yet. An item that already carries this UPC is counted
  as already linked; one carrying a different UPC is left alone and listed.

Writes two fields and nothing else: ``upcCode`` and
``custitem_sanmar_unique_key``. It never sets an external id and never renames
a colour (Andy, 2026-11-20).

Capped by ``LINK_MAX_ITEMS`` (falls back to ``MAX_ITEMS``; default 25, 0 = no
cap). With a cap, styles are matched in order only until enough clean items
are found, so a small batch does not wait for the whole catalogue.

Honors ``SYNC_DRY_RUN``: a dry run reports what it would link.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import field_policy
from concurrent_writes import write_records
from run_status import exit_code, leg_deadline, past_deadline
from sanmar_link_report import analyse, sanmar_vendor_items

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.adopt import OptionMaps, match_existing
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp

LABEL = "LINK APPLY"
KEY_FIELD = "custitem_sanmar_unique_key"
STYLE_CHUNK = 200
WRITE_SLICE = 500
LIST_MAX = 50


def _cap() -> int:
    raw = os.environ.get("LINK_MAX_ITEMS") or os.environ.get("MAX_ITEMS") or "25"
    return max(0, int(raw or "0"))


def plan(client, styles, vendor_items: set[str], cap: int) -> dict:
    """Pick the clean matches to link, in feed order, up to ``cap``."""
    jobs: list[dict] = []
    already = 0
    other_upc: list[dict] = []
    checked = 0
    for start in range(0, len(styles), STYLE_CHUNK):
        chunk = styles[start : start + STYLE_CHUNK]
        report = match_existing(client, chunk)
        a = analyse(report, OptionMaps(client), vendor_items)
        clean = set(a["clean"])
        checked += len(chunk)
        for row in report.matched:
            if row.ns_id not in clean or not row.gtin:
                continue
            item = report.claimed_items[row.ns_id]
            entry = {"id": row.ns_id, "item": item.itemid, "style": row.style,
                     "color": row.color_name, "size": row.size,
                     "upc": row.gtin, "key": row.unique_key,
                     "current_upc": item.upc}
            if item.upc == row.gtin:
                already += 1
            elif item.upc:
                other_upc.append(entry)
            elif not cap or len(jobs) < cap:
                jobs.append(entry)
        if cap and len(jobs) >= cap:
            break
    return {"jobs": jobs, "already": already, "other_upc": other_upc,
            "styles_checked": checked}


def read_back(client, jobs: list[dict]) -> int:
    """How many of ``jobs`` now carry their UPC, read from NetSuite."""
    good = 0
    want = {j["id"]: j["upc"] for j in jobs}
    ids = list(want)
    for i in range(0, len(ids), 500):
        in_list = ", ".join(ids[i : i + 500])
        for r in client.suiteql(
            f"SELECT id, upccode FROM item WHERE id IN ({in_list})"
        ):
            if str(r.get("upccode") or "").strip() == want.get(str(r["id"])):
                good += 1
    return good


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    cap = _cap()
    verb = "linked" if allow_write else "WOULD link (dry run)"

    catalog = Path(cfg.sftp.download_dir) / C.FILE_SDL_N
    if not catalog.exists():
        catalog = SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)
    styles = list(parse_styles(catalog))
    client = NetSuiteClient(cfg.netsuite)

    vendor_items = sanmar_vendor_items(client)
    if vendor_items is None:
        print(f"WARNING: {LABEL}: Sanmar vendor lines could not be read, so "
              "no item can be confirmed as Sanmar's. Nothing was linked.")
        return 1
    has_key = field_policy.exists(client, KEY_FIELD)
    if not has_key:
        print(f"NOTE: {LABEL}: {KEY_FIELD} is not in this account; only the "
              "UPC is written")

    p = plan(client, styles, vendor_items, cap)
    jobs = p["jobs"]
    print(f"{LABEL}: cap {cap or 'none'}; styles checked: "
          f"{p['styles_checked']:,} of {len(styles):,}; clean items to link: "
          f"{len(jobs):,}; already linked: {p['already']:,}; "
          f"left alone, different UPC on the item: {len(p['other_upc']):,}")
    for e in p["other_upc"][:LIST_MAX]:
        print(f"{LABEL}: left alone: item {e['id']} {e['item']} has UPC "
              f"{e['current_upc']}, feed says {e['upc']}")

    written = failures = 0
    other = [0]

    def _on_err(rid: str, exc: Exception) -> None:
        if "429" not in str(exc):
            other[0] += 1
        print(f"FAILED item {rid}: {str(exc)[:160]}")

    done: list[dict] = []
    if allow_write:
        deadline = leg_deadline()
        for i in range(0, len(jobs), WRITE_SLICE):
            if past_deadline(deadline):
                print(f"NOTE: {LABEL}: stopped at the time limit; "
                      f"{len(jobs) - i:,} item(s) not reached. The next run "
                      "carries on.")
                break
            part = jobs[i : i + WRITE_SLICE]
            bodies = []
            for e in part:
                body = {"upcCode": e["upc"]}
                if has_key:
                    body[KEY_FIELD] = e["key"]
                bodies.append((e["id"], body))
            w, f = write_records(client, "inventoryItem", bodies,
                                 on_error=_on_err)
            written += w
            failures += f
            done += part
    else:
        written, done = len(jobs), jobs

    if len(done) <= LIST_MAX:
        for e in done:
            print(f"{LABEL}: {verb}: item {e['id']} {e['item']} "
                  f"({e['color']}, {e['size']}) UPC {e['upc']} "
                  f"Sanmar key {e['key']}")

    out = Path("data/sanmar_link_applied.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["NetSuite Item Id", "NetSuite Item", "Style", "Feed Color",
                    "Feed Size", "UPC", "Sanmar Unique Key"])
        for e in done:
            w.writerow([e["id"], e["item"], e["style"], e["color"], e["size"],
                        e["upc"], e["key"]])

    print(f"{LABEL}: {verb} {written:,} item(s); failures: {failures:,}")
    if allow_write and done:
        good = read_back(client, done)
        print(f"{LABEL}: read back from NetSuite: {good:,} of {len(done):,} "
              "item(s) carry the UPC")
        if good < len(done) - failures:
            print(f"WARNING: {LABEL}: {len(done) - failures - good:,} item(s) "
                  "were accepted but do not show the UPC")
            return 1
    return exit_code(LABEL, failures, other[0], len(done))


if __name__ == "__main__":
    raise SystemExit(main())
