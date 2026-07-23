"""Correct Department + Class on every SanMar-matched item -- not just fill
blanks, but fix wrong values too.

Department/Class are currently only set at item-CREATE time (see
csv_export.py / restlet_payload.py / sanmar_parent_create.py); nothing ever
revisits existing items. Two problems that leaves in place:

1. SanMar's feed CATEGORY_NAME is a semicolon-delimited multi-tag string (e.g.
   "T-Shirts ;Tall;Activewear"), which the OLD exact-match category table
   almost never matched -- ~46% of SKUs got a blank Class (see
   class_for_category's docstring / the category probe). class_for_category
   now handles this; this script re-applies it everywhere.
2. child-finalize only *copies* Department/Class from the parent when the
   CHILD is blank -- it never corrects a PARENT whose category was unmapped
   at creation, so a wrong/blank value on the parent just propagates forever.

This script computes the correct Department/Class independently for every
item carrying a custitem_sanmar_style key (parent or child, from the SAME
feed-category lookup) and PATCHes whenever it differs from what's currently
set -- a correction, not a fill-blanks-only pass.

Diff-aware after the id-resolution step; honors SYNC_DRY_RUN;
UPDATE_MAX_ITEMS caps writes; uses the shared concurrent_writes helper.
"""

from __future__ import annotations

import os
from pathlib import Path

from concurrent_writes import write_records

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.sanmar import constants as C
from sanmar_netsuite.sanmar.parsers import parse_styles
from sanmar_netsuite.sanmar.sftp_client import SanMarSftp
from sanmar_netsuite.transform.csv_export import DEFAULT_DEPARTMENT, class_for_category

STYLE_FIELD = "custitem_sanmar_style"


def _resolve_feed(cfg) -> Path:
    local = Path(cfg.sftp.download_dir) / C.FILE_SDL_N
    return local if local.exists() else SanMarSftp(cfg.sftp).download(C.FILE_SDL_N)


def resolve_department_id(client: NetSuiteClient, name: str) -> str | None:
    rows = client.suiteql(f"SELECT id FROM department WHERE name = '{_sql_escape(name)}'")
    return str(rows[0]["id"]) if rows else None


def resolve_class_ids(client: NetSuiteClient, names: set[str]) -> dict[str, str]:
    """NetSuite Class list -> SuiteQL table 'classification'; match on
    fullname (the "Parent : Child" path our mapping already uses)."""
    out: dict[str, str] = {}
    for name in names:
        if not name:
            continue
        try:
            rows = client.suiteql(
                f"SELECT id FROM classification WHERE fullname = '{_sql_escape(name)}'"
            )
        except Exception as exc:  # noqa: BLE001 - table/column name unconfirmed; report, don't crash
            print(f"  WARNING: classification lookup failed for {name!r}: {str(exc)[:150]}")
            continue
        if rows:
            out[name] = str(rows[0]["id"])
        else:
            print(f"  WARNING: no NetSuite Class found matching fullname={name!r}")
    return out


def plan_body(
    row: dict,
    *,
    correct_class: str,
    dept_id: str | None,
    class_id: str | None,
) -> dict:
    """REST body to correct one item's department/class.

    Writes whenever the computed-correct value differs from what SuiteQL
    reports (a correction, not fill-blanks-only) -- but only when we
    successfully resolved an id for the target value; an unresolved class
    name is left alone rather than guessed or cleared.
    """
    body: dict[str, object] = {}
    if dept_id and str(row.get("department") or "") != dept_id:
        body["department"] = {"id": dept_id}
    if correct_class and class_id and str(row.get("class") or "") != class_id:
        body["class"] = {"id": class_id}
    return body


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    max_items = int(os.environ.get("UPDATE_MAX_ITEMS", "0") or "0")
    client = NetSuiteClient(cfg.netsuite)

    styles = parse_styles(_resolve_feed(cfg))
    category_by_style = {s.style: s.category for s in styles}
    class_by_style = {
        style: cls for style, cat in category_by_style.items()
        if (cls := class_for_category(cat))
    }
    print(f"styles: {len(styles):,}  with a resolvable class: {len(class_by_style):,}")

    dept_id = resolve_department_id(client, DEFAULT_DEPARTMENT)
    print(f"department {DEFAULT_DEPARTMENT!r} -> {dept_id!r}")
    class_ids = resolve_class_ids(client, set(class_by_style.values()))
    print(f"resolved {len(class_ids)}/{len(set(class_by_style.values()))} distinct Class name(s)")

    rows = client.suiteql(
        f"SELECT id, {STYLE_FIELD}, department, class FROM item "
        f"WHERE {STYLE_FIELD} IS NOT NULL"
    )
    print(f"SanMar-keyed items in NetSuite: {len(rows):,}")

    considered = written = unchanged = nostyle = noclass = failures = 0
    samples = 0
    write_jobs: list[tuple[str, dict]] = []
    for row in rows:
        style = str(row.get(STYLE_FIELD) or "").strip()
        if style not in category_by_style:
            nostyle += 1
            continue
        correct_class = class_by_style.get(style, "")
        if not correct_class:
            noclass += 1
            continue
        body = plan_body(
            row,
            correct_class=correct_class,
            dept_id=dept_id,
            class_id=class_ids.get(correct_class),
        )
        if not body:
            unchanged += 1
            continue
        if max_items and considered >= max_items:
            continue
        considered += 1
        if samples < 15:
            samples += 1
            print(f"  item {row.get('id')} (style {style}): set {list(body)} "
                  f"(class -> {correct_class!r})")
        if not allow_write:
            written += 1
            continue
        write_jobs.append((str(row["id"]), body))

    def _on_err(rid: str, exc: Exception) -> None:
        nonlocal failures
        failures += 1
        if failures <= 10:
            detail = getattr(exc, "payload", "")
            print(f"  FAILED item {rid}: {str(exc)[:150]} :: {str(detail)[:300]}")

    if allow_write and write_jobs:
        w, f = write_records(client, "inventoryItem", write_jobs, on_error=_on_err)
        written += w
        failures += f

    verb = "wrote" if allow_write else "WOULD write (dry run)"
    print(f"\ndepartment/class fix: {verb} {written} item(s); unchanged: {unchanged}; "
          f"no style match: {nostyle}; no resolvable class: {noclass}; failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
