"""Upload File Cabinet files via SuiteTalk SOAP (REST has no 'file' record).

Mirrors the reference/upload split in ``files.py``, but for the SOAP ``add``
operation, which is the only interface that can create ``File`` records.
"""

from __future__ import annotations

import base64
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from ns_soap import VERSION, is_success, post  # noqa: E402

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SoapUploadedFile:
    file_id: str
    name: str


def _file_type_for(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".png"):
        return "_PNGIMAGE"
    if lower.endswith(".gif"):
        return "_GIFIMAGE"
    if lower.endswith((".jpg", ".jpeg")):
        return "_JPGIMAGE"
    return "_JPGIMAGE"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8))
def _fetch(url: str, timeout: int) -> bytes:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def upload_from_url_soap(
    cfg, url: str, filename: str, folder_id: str, *, timeout: int = 60
) -> SoapUploadedFile:
    """Download ``url`` and create a File Cabinet record for it via SOAP."""
    content = _fetch(url, timeout)
    b64 = base64.b64encode(content).decode("ascii")
    doc_ns = f"urn:filecabinet_{VERSION}.documents.webservices.netsuite.com"
    body = f"""
    <platformMsgs:add xmlns:documents="{doc_ns}">
      <platformMsgs:record xsi:type="documents:File">
        <documents:name>{escape(filename)}</documents:name>
        <documents:fileType>{_file_type_for(filename)}</documents:fileType>
        <documents:folder internalId="{folder_id}"/>
        <documents:isOnline>true</documents:isOnline>
        <documents:content>{b64}</documents:content>
      </platformMsgs:record>
    </platformMsgs:add>"""
    text = post(cfg, "add", body)
    if not is_success(text):
        raise RuntimeError(f"SOAP file upload failed: {text[:1500]}")
    m = re.search(r'internalId="(\d+)"[^>]*type="file"', text)
    if not m:
        m = re.search(r"<platformCore:internalId>(\d+)</platformCore:internalId>", text)
    if not m:
        raise RuntimeError(f"upload succeeded but no internalId found: {text[:800]}")
    file_id = m.group(1)
    log.info("SOAP-uploaded %s -> File Cabinet id %s", filename, file_id)
    return SoapUploadedFile(file_id=file_id, name=filename)
