"""Upload the merged Champro reference PDFs (sizing guide + fabrics) to the
NetSuite File Cabinet and print their login-free media URLs, so they can be
linked from Champro custom fields.

Idempotent: a same-name file already in the target folder is reused rather than
re-uploaded. Uploads only when SYNC_DRY_RUN=false; sandbox-gated like every
other writer.
"""

from __future__ import annotations

from pathlib import Path

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient
from sanmar_netsuite.netsuite.repository import _sql_escape
from sanmar_netsuite.netsuite.soap_files import create_folder_soap, upload_bytes_soap

ROOT = Path(__file__).resolve().parents[1]
FOLDER_NAME = "Champro Reference Docs"
DOCS = [
    ("Champro Sizing Guide.pdf", ROOT / "data" / "champro" / "ChamproSizingGuide.pdf"),
    ("Champro Fabrics.pdf", ROOT / "data" / "champro" / "ChamproFabrics.pdf"),
]


def _abs_url(url: str, account: str) -> str:
    if url.startswith("/"):
        return f"https://{account.replace('_', '-').lower()}.app.netsuite.com{url}"
    return url


def _file_url(client: NetSuiteClient, file_id: str, account: str) -> str:
    try:
        rows = client.suiteql(f"SELECT url FROM file WHERE id = {file_id}")
    except Exception:  # noqa: BLE001 - url column not exposed in every account
        return ""
    return _abs_url(str(rows[0].get("url") or ""), account) if rows else ""


def main() -> int:
    cfg = get_config()
    allow_write = not cfg.sync.dry_run
    nscfg = cfg.netsuite
    account = nscfg.account_id
    client = NetSuiteClient(nscfg)

    rows = client.suiteql(
        f"SELECT id FROM mediaitemfolder WHERE name = '{_sql_escape(FOLDER_NAME)}'"
    )
    if rows:
        folder_id = str(rows[0]["id"])
        print(f"folder {FOLDER_NAME!r} -> id {folder_id}")
    elif allow_write:
        folder_id = create_folder_soap(nscfg, FOLDER_NAME)
        print(f"created folder {FOLDER_NAME!r} -> id {folder_id}")
    else:
        folder_id = ""
        print(f"[dry] would create folder {FOLDER_NAME!r}")

    print("\n==== Champro reference doc URLs (copy these) ====")
    for display, path in DOCS:
        if not path.exists():
            print(f"  MISSING {path}")
            continue
        existing = []
        if folder_id.isdigit():
            existing = client.suiteql(
                f"SELECT id FROM file WHERE name = '{_sql_escape(display)}' "
                f"AND folder = {folder_id}"
            )
        if existing:
            fid = str(existing[0]["id"])
            print(f"  {display}: EXISTS id {fid}  URL: {_file_url(client, fid, account)}")
            continue
        size = path.stat().st_size
        if not allow_write:
            print(f"  [dry] would upload {display} ({size:,} bytes) to folder {folder_id or '?'}")
            continue
        up = upload_bytes_soap(nscfg, path.read_bytes(), display, folder_id)
        url = _file_url(client, up.file_id, account)
        print(f"  {display}: UPLOADED id {up.file_id}  URL: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
