"""Drive the NetSuite CSV import of the create-only child CSV via the
``bsg_csv_import`` RESTlet, then poll each job to completion.

SuiteTalk has no "run a saved CSV import" call, so this posts each
``data/sanmar_new_children_part*.csv`` to the RESTlet, which writes it to the
File Cabinet and submits an import against the saved map. This is the automated
alternative to hand-importing through the Import Assistant.

Env (the secrets from docs/CSV_AUTOCREATE.md):
  NETSUITE_CSVIMPORT_SCRIPT_ID / _DEPLOY_ID  -- the RESTlet's script + deploy ids
  NETSUITE_CSVIMPORT_FOLDER_ID               -- File Cabinet folder to stage CSVs
  NETSUITE_CSVIMPORT_CHILD_MAP               -- saved import map id (Add mode)

Honors ``SYNC_DRY_RUN`` (dry = report what would import, no RESTlet call). Parts
import sequentially, each polled to a terminal status before the next.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

ROOT = Path(__file__).resolve().parents[1]
POLL_SECONDS = 30
POLL_MAX = 60  # up to ~30 min per part
_TERMINAL = {"COMPLETE", "FAILED", "CANCELLED", "NOTINITIATED"}


def build_job(mapping_id: str, folder_id: str, csv_text: str, name: str) -> dict:
    """One RESTlet job: inline CSV -> File Cabinet -> import against the map."""
    return {
        "mappingId": mapping_id,
        "folderId": int(folder_id),
        "fileName": f"{name}.csv",
        "name": name,
        "csv": csv_text,
    }


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"{name} is not set (see docs/CSV_AUTOCREATE.md)")
    return val


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    stamp = os.environ.get("IMPORT_STAMP", "run")  # date passed by CI (no clock here)

    parts = sorted((ROOT / "data").glob("sanmar_new_children_part*.csv"))
    if not parts:
        print("no data/sanmar_new_children_part*.csv to import "
              "(run create-preview first) -- nothing to do")
        return 0
    total_rows = sum(sum(1 for _ in p.open(encoding="utf-8")) - 1 for p in parts)
    print(f"{len(parts)} part(s), {total_rows} row(s) to import")

    if not allow_write:
        for p in parts:
            n = sum(1 for _ in p.open(encoding="utf-8")) - 1
            print(f"  WOULD import {p.name}: {n} rows (dry run)")
        print("dry run -- no RESTlet call made")
        return 0

    script_id = _env("NETSUITE_CSVIMPORT_SCRIPT_ID")
    deploy_id = _env("NETSUITE_CSVIMPORT_DEPLOY_ID")
    folder_id = _env("NETSUITE_CSVIMPORT_FOLDER_ID")
    mapping_id = _env("NETSUITE_CSVIMPORT_CHILD_MAP")
    client = NetSuiteClient(cfg.netsuite)

    failures = 0
    for i, part in enumerate(parts, 1):
        name = f"sanmar_new_children_{stamp}_part{i:02d}"
        job = build_job(mapping_id, folder_id, part.read_text(encoding="utf-8"), name)
        resp = client.call_restlet(script_id, deploy_id, {"jobs": [job]})
        result = (resp.get("results") or [{}])[0]
        if not result.get("ok"):
            failures += 1
            print(f"  {part.name}: submit FAILED -- {result.get('error')}")
            continue
        task_id = result.get("taskId")
        print(f"  {part.name}: submitted (file {result.get('fileId')}, task {task_id}); polling…")
        status = _poll(client, script_id, deploy_id, str(task_id))
        print(f"  {part.name}: final status = {status}")
        if status != "COMPLETE":
            failures += 1

    print(f"\ncsv import: {len(parts) - failures}/{len(parts)} part(s) COMPLETE; "
          f"failures: {failures}")
    return 1 if failures else 0


def _poll(client: NetSuiteClient, script_id: str, deploy_id: str, task_id: str) -> str:
    for _ in range(POLL_MAX):
        resp = client.call_restlet(
            script_id, deploy_id, None, method="GET", params={"taskId": task_id}
        )
        status = str(resp.get("status") or "").upper()
        if status in _TERMINAL:
            return status
        time.sleep(POLL_SECONDS)
    return "TIMEOUT"


if __name__ == "__main__":
    raise SystemExit(main())
