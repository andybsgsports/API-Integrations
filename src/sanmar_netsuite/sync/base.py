"""Shared sync result + reporting helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Tally of what a sync run did. Returned by every sync entrypoint."""

    entity: str
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped_unchanged: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def record_failure(self, key: str, message: str) -> None:
        self.failed += 1
        self.errors.append(f"{key}: {message}")
        log.error("[%s] failed for %s: %s", self.entity, key, message)

    def summary(self) -> str:
        return (
            f"{self.entity}: processed={self.processed} created={self.created} "
            f"updated={self.updated} skipped={self.skipped_unchanged} failed={self.failed}"
        )

    def merge(self, other: SyncResult) -> None:
        self.processed += other.processed
        self.created += other.created
        self.updated += other.updated
        self.skipped_unchanged += other.skipped_unchanged
        self.failed += other.failed
        self.errors.extend(other.errors)
