"""Image sync.

Default strategy is **reference**: the catalog sync already writes the SanMar
CDN image URL onto a custom item field, so no separate work is needed.

This module implements the optional **upload** strategy: pull image bytes from
the SanMar URLs and create File Cabinet records, attaching the resulting file to
each style's parent item. Use when images must live inside NetSuite.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import AppConfig
from ..netsuite.files import ImageUploader
from ..netsuite.repository import ItemRepository, parent_external_id
from ..sanmar.parsers import parse_styles
from ..state.cache import StateCache, hash_payload
from .base import SyncResult

log = logging.getLogger(__name__)


def sync_images(
    file_path: str | Path,
    config: AppConfig,
    folder_id: str,
    *,
    repo: ItemRepository | None = None,
    uploader: ImageUploader | None = None,
    cache: StateCache | None = None,
) -> SyncResult:
    """Upload one representative image per style/color into the File Cabinet."""
    result = SyncResult(entity="images")
    styles = parse_styles(file_path)
    owns_cache = cache is None
    cache = cache or StateCache(config.sync.state_db_path)
    dry_run = config.sync.dry_run
    if not dry_run and (repo is None or uploader is None):
        raise ValueError("repo and uploader are required when not in dry-run mode")

    try:
        for style in styles:
            for color, images in style.images_by_color.items():
                url = images.primary_url()
                if not url:
                    continue
                if config.sync.max_records and result.processed >= config.sync.max_records:
                    break
                result.processed += 1
                filename = _filename_for(style.style, color, url)
                marker = hash_payload({"style": style.style, "color": color, "url": url})
                key = f"{style.style}:{color}"
                if cache.is_unchanged("image", key, marker):
                    result.skipped_unchanged += 1
                    continue
                if dry_run:
                    log.debug("[dry-run] image %s <- %s", filename, url)
                    result.updated += 1
                    continue
                assert repo is not None and uploader is not None  # non-dry-run invariant
                try:
                    uploaded = uploader.upload_from_url(url, filename)
                    # Attach to the parent item's image field.
                    parent_eid = parent_external_id(style.style)
                    internal_id = cache.get_netsuite_id(
                        "style", parent_eid
                    ) or repo.find_id_by_external_id(parent_eid)
                    if internal_id:
                        repo.update_fields(
                            internal_id,
                            {config.netsuite.fields.image_file: {"id": uploaded.file_id}},
                        )
                    cache.upsert("image", key, marker, uploaded.file_id)
                    result.created += 1
                except Exception as exc:  # noqa: BLE001
                    result.record_failure(key, str(exc))
    finally:
        if owns_cache:
            cache.close()

    log.info(result.summary())
    return result


def _filename_for(style: str, color: str, url: str) -> str:
    ext = Path(url.split("?")[0]).suffix or ".jpg"
    safe_color = color.replace(" ", "_").replace("/", "-")
    return f"{style}_{safe_color}{ext}"
