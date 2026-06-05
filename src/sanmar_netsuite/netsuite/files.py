"""Image handling for NetSuite.

Two strategies are supported, selectable per run:

* **reference** (default, cheap): store the SanMar CDN image URL on a custom
  item field. No bytes move; NetSuite just points at SanMar's hosted image.
* **upload**: fetch the image bytes from the SanMar URL and create a File
  Cabinet record, then attach it to the item. Use when you need the images to
  live inside NetSuite (e.g. for SuiteCommerce served from File Cabinet).

The File Cabinet ``file`` record is created through the SuiteTalk REST record
API with base64-encoded content.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .client import NetSuiteClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadedImage:
    file_id: str
    name: str
    url: str


class ImageUploader:
    def __init__(self, client: NetSuiteClient, folder_id: str, *, timeout: int = 60) -> None:
        self._client = client
        self._folder_id = folder_id
        self._timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=8))
    def _fetch(self, url: str) -> bytes:
        resp = requests.get(url, timeout=self._timeout)
        resp.raise_for_status()
        return resp.content

    def upload_from_url(self, url: str, filename: str) -> UploadedImage:
        """Download an image from ``url`` and create a File Cabinet record."""
        content = self._fetch(url)
        body = {
            "name": filename,
            "fileType": _file_type_for(filename),
            "folder": {"id": self._folder_id},
            "contents": base64.b64encode(content).decode("ascii"),
        }
        file_id = self._client.create_record("file", body)
        log.info("Uploaded image %s -> File Cabinet id %s", filename, file_id)
        return UploadedImage(file_id=file_id, name=filename, url=url)


def _file_type_for(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".png"):
        return "PNGIMAGE"
    if lower.endswith(".gif"):
        return "GIFIMAGE"
    if lower.endswith((".jpg", ".jpeg")):
        return "JPGIMAGE"
    if lower.endswith(".pdf"):
        return "PDF"
    return "JPGIMAGE"
