"""Fetch a Suitelet's source (and folder siblings) from the File Cabinet.

The Team Ordering Center storefront (script id 2358) lives only in NetSuite's
File Cabinet -- no copy in this repo -- so changing it means round-tripping
the source. REST has no file-content endpoint, but SOAP ``get`` on a ``file``
record returns the content base64-encoded, using the same TBA passport the
field-creation tooling already proved out.

Resolves the script record's file via SuiteQL, then fetches every file in the
same folder (the Suitelet may split HTML/CSS/JS into siblings), writing them
under ``suitescript/store/``. The workflow commits the result back to the
branch, giving the repo an editable copy.

Read-only against NetSuite.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import requests
from ns_field_create_soap import VERSION, _passport

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "suitescript" / "store"

SCRIPT_ID = int(os.environ.get("FILE_FETCH_SCRIPT_ID", "2358") or "2358")
MAX_FILES = 20
MAX_BYTES = 2_000_000


def soap_get_file(cfg, internal_id: str) -> tuple[str, bytes] | None:
    """(name, content bytes) for one File Cabinet file, or None on failure."""
    url_account = cfg.account_id.replace("_", "-").lower()
    url = f"https://{url_account}.suitetalk.api.netsuite.com/services/NetSuitePort_{VERSION}"
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:platformMsgs="urn:messages_{VERSION}.platform.webservices.netsuite.com"
    xmlns:platformCore="urn:core_{VERSION}.platform.webservices.netsuite.com">
  <soapenv:Header>{_passport(cfg)}
  </soapenv:Header>
  <soapenv:Body>
    <platformMsgs:get>
      <platformMsgs:baseRef xsi:type="platformCore:RecordRef"
          internalId="{internal_id}" type="file"/>
    </platformMsgs:get>
  </soapenv:Body>
</soapenv:Envelope>"""
    resp = requests.post(
        url,
        data=envelope.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "get"},
        timeout=120,
    )
    text = resp.text
    if 'isSuccess="true"' not in text:
        print(f"  file {internal_id}: SOAP get FAILED "
              f"(HTTP {resp.status_code}): {text[:300]!r}")
        return None
    name_m = re.search(r"<(?:\w+:)?name>([^<]+)</(?:\w+:)?name>", text)
    content_m = re.search(r"<(?:\w+:)?content>([^<]+)</(?:\w+:)?content>", text)
    if not content_m:
        print(f"  file {internal_id}: success but no <content> in response")
        return None
    name = name_m.group(1) if name_m else f"file_{internal_id}"
    return name, base64.b64decode(content_m.group(1))


def main() -> int:
    cfg = get_config().netsuite
    client = NetSuiteClient(cfg)

    rows = client.suiteql(
        f"SELECT id, name, scriptfile FROM script WHERE id = {SCRIPT_ID}"
    )
    if not rows:
        print(f"no script record with id {SCRIPT_ID}")
        return 1
    script = rows[0]
    file_id = str(script.get("scriptfile") or "")
    print(f"script {SCRIPT_ID}: {script.get('name')!r} -> scriptfile {file_id}")
    if not file_id:
        print("script record carries no scriptfile reference")
        return 1

    folder_rows = client.suiteql(
        f"SELECT folder FROM file WHERE id = {file_id}"
    )
    folder = str(folder_rows[0].get("folder") or "") if folder_rows else ""
    targets = [(file_id, None)]
    if folder:
        siblings = client.suiteql(
            "SELECT id, name, filesize FROM file "
            f"WHERE folder = {folder} ORDER BY name"
        )
        print(f"folder {folder}: {len(siblings)} file(s)")
        for s in siblings:
            sid = str(s["id"])
            size = int(s.get("filesize") or 0)
            if sid == file_id:
                continue
            if size > MAX_BYTES:
                print(f"  skip {s.get('name')} ({size:,} bytes > cap)")
                continue
            targets.append((sid, s.get("name")))
    targets = targets[:MAX_FILES]

    DEST.mkdir(parents=True, exist_ok=True)
    fetched = 0
    for fid, _hint in targets:
        got = soap_get_file(cfg, fid)
        if not got:
            continue
        name, content = got
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        out = DEST / safe
        out.write_bytes(content)
        fetched += 1
        print(f"  wrote suitescript/store/{safe} ({len(content):,} bytes)")

    print(f"\nfetched {fetched} of {len(targets)} file(s) into suitescript/store/")
    return 0 if fetched else 1


if __name__ == "__main__":
    raise SystemExit(main())
