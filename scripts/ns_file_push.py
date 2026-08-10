"""Push edited storefront sources back into the File Cabinet (SOAP update).

The counterpart to ns_file_fetch.py: the Team Ordering Center Suitelet and its
client/lib files live only in NetSuite, so repo edits go live by updating the
File Cabinet file records' content. SOAP ``update`` on a ``file`` record with
base64 content is the only API path (REST has no file-content endpoint), using
the same TBA passport as the rest of the SOAP tooling.

Deliberate safety rails:
  - Only pushes files named in FILE_PUSH_FILES (comma-separated, relative to
    suitescript/store/) -- nothing is pushed implicitly.
  - Targets are resolved by name within the script's own folder (plus one
    subfolder level), never by caller-supplied internal ids.
  - Each update is verified by re-fetching the file and comparing content;
    a mismatch fails the run loudly.
  - The pre-edit content is always recoverable from git history (the fetch
    workflow committed the originals before any edit).
"""

from __future__ import annotations

import base64
import os
import re
import sys
from pathlib import Path

import requests
from ns_field_create_soap import VERSION, _passport
from ns_file_fetch import soap_get_file

from sanmar_netsuite.config import get_config
from sanmar_netsuite.netsuite.client import NetSuiteClient

ROOT = Path(__file__).resolve().parents[1]
# Repo directory the pushed paths are relative to. Default is the storefront
# tree; FILE_PUSH_SRC=suitescript pushes RESTlet sources (e.g.
# bsg_sanmar_matrix.js) anchored by that script's own id.
SRC = ROOT / os.environ.get("FILE_PUSH_SRC", "suitescript/store")

SCRIPT_ID = int(os.environ.get("FILE_PUSH_SCRIPT_ID", "2358") or "2358")
FILES = [
    p.strip()
    for p in (os.environ.get("FILE_PUSH_FILES") or "").split(",")
    if p.strip()
]
MAX_BYTES = 2_000_000


def soap_update_file(cfg, internal_id: str, content: bytes) -> tuple[bool, str]:
    """Replace one File Cabinet file's content. Returns (ok, detail)."""
    url_account = cfg.account_id.replace("_", "-").lower()
    url = f"https://{url_account}.suitetalk.api.netsuite.com/services/NetSuitePort_{VERSION}"
    b64 = base64.b64encode(content).decode()
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:platformMsgs="urn:messages_{VERSION}.platform.webservices.netsuite.com"
    xmlns:platformCore="urn:core_{VERSION}.platform.webservices.netsuite.com"
    xmlns:docFileCab="urn:filecabinet_{VERSION}.documents.webservices.netsuite.com">
  <soapenv:Header>{_passport(cfg)}
  </soapenv:Header>
  <soapenv:Body>
    <platformMsgs:update>
      <platformMsgs:record xsi:type="docFileCab:File" internalId="{internal_id}">
        <docFileCab:content>{b64}</docFileCab:content>
      </platformMsgs:record>
    </platformMsgs:update>
  </soapenv:Body>
</soapenv:Envelope>"""
    resp = requests.post(
        url,
        data=envelope.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "update"},
        timeout=180,
    )
    if 'isSuccess="true"' in resp.text:
        return True, ""
    return False, f"HTTP {resp.status_code}: {resp.text[:400]!r}"


def build_target_map(client, file_id: str) -> dict[str, str]:
    """(repo-relative path under suitescript/store) -> File Cabinet internal id.

    Mirrors the fetch layout: files in the script's folder map to their bare
    name; files one subfolder down map to "<subfolder>/<name>".
    """
    targets: dict[str, str] = {}
    folder_rows = client.suiteql(f"SELECT folder FROM file WHERE id = {file_id}")
    folder = str(folder_rows[0].get("folder") or "") if folder_rows else ""
    if not folder:
        return targets
    for f in client.suiteql(
        f"SELECT id, name FROM file WHERE folder = {folder}"
    ):
        targets[str(f.get("name") or "")] = str(f["id"])
    for sub in client.suiteql(
        f"SELECT id, name FROM mediaitemfolder WHERE parent = {folder}"
    ):
        sub_name = re.sub(r"[^A-Za-z0-9._-]", "_", str(sub.get("name") or ""))
        for f in client.suiteql(
            f"SELECT id, name FROM file WHERE folder = {sub['id']}"
        ):
            targets[f"{sub_name}/{f.get('name')}"] = str(f["id"])
    return targets


def main() -> int:
    if not FILES:
        print("FILE_PUSH_FILES is empty -- nothing to push (explicit list required)")
        return 1

    cfg = get_config().netsuite
    client = NetSuiteClient(cfg)

    rows = client.suiteql(
        f"SELECT id, name, scriptfile FROM script WHERE id = {SCRIPT_ID}"
    )
    if not rows or not rows[0].get("scriptfile"):
        print(f"cannot resolve script {SCRIPT_ID} / its scriptfile")
        return 1
    file_id = str(rows[0]["scriptfile"])
    print(f"script {SCRIPT_ID}: {rows[0].get('name')!r} -> scriptfile {file_id}")

    targets = build_target_map(client, file_id)
    print(f"target map: {len(targets)} file(s) in the script's folder tree")

    failures = 0
    for rel in FILES:
        local = SRC / rel
        if not local.is_file():
            print(f"  {rel}: MISSING locally ({local}) -- skipped")
            failures += 1
            continue
        content = local.read_bytes()
        if len(content) > MAX_BYTES:
            print(f"  {rel}: {len(content):,} bytes exceeds cap -- skipped")
            failures += 1
            continue
        fid = targets.get(rel)
        if not fid:
            print(f"  {rel}: no matching File Cabinet file in the script folder -- skipped")
            failures += 1
            continue
        ok, detail = soap_update_file(cfg, fid, content)
        if not ok:
            print(f"  {rel} -> file {fid}: UPDATE FAILED {detail}")
            failures += 1
            continue
        # Verify: read it back and compare bytes.
        got = soap_get_file(cfg, fid)
        if not got or got[1] != content:
            back = len(got[1]) if got else "none"
            print(
                f"  {rel} -> file {fid}: VERIFY FAILED "
                f"(pushed {len(content):,} bytes, read back {back})"
            )
            failures += 1
            continue
        print(f"  {rel} -> file {fid}: updated + verified ({len(content):,} bytes)")

    if failures:
        print(f"\n{failures} file(s) failed")
        return 1
    print(f"\npushed {len(FILES)} file(s)")
    return 0


if __name__ == "__main__":
    sys.stdout.flush()
    raise SystemExit(main())
