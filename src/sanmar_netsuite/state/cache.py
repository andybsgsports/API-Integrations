"""SQLite-backed delta cache.

SanMar's full catalog is large and changes incrementally day to day. To avoid
pushing every record on every run we hash the canonical payload we *would*
send to NetSuite and store it keyed by ``(entity, key)``. On the next run, a
record whose hash is unchanged is skipped.

The cache also remembers the NetSuite internal id assigned to each SanMar key,
so later syncs (pricing, inventory) can target the right record without an
extra lookup.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_state (
    entity      TEXT NOT NULL,        -- 'style' | 'sku' | 'pricing' | 'inventory' | 'image'
    key         TEXT NOT NULL,        -- SanMar style or unique_key
    payload_hash TEXT NOT NULL,
    netsuite_id TEXT,                 -- internal id once known
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (entity, key)
);
CREATE INDEX IF NOT EXISTS idx_sync_state_ns ON sync_state(entity, netsuite_id);
"""


def hash_payload(payload: Any) -> str:
    """Stable SHA-256 of a JSON-serializable payload."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class StateCache:
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, entity: str, key: str) -> tuple[str, str | None] | None:
        """Return ``(payload_hash, netsuite_id)`` for a key, or ``None``."""
        cur = self._conn.execute(
            "SELECT payload_hash, netsuite_id FROM sync_state WHERE entity=? AND key=?",
            (entity, key),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else None

    def is_unchanged(self, entity: str, key: str, payload_hash: str) -> bool:
        existing = self.get(entity, key)
        return existing is not None and existing[0] == payload_hash

    def upsert(
        self,
        entity: str,
        key: str,
        payload_hash: str,
        netsuite_id: str | None = None,
    ) -> None:
        # Preserve a previously known netsuite_id if the caller doesn't supply one.
        if netsuite_id is None:
            existing = self.get(entity, key)
            if existing is not None:
                netsuite_id = existing[1]
        self._conn.execute(
            """
            INSERT INTO sync_state (entity, key, payload_hash, netsuite_id, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(entity, key) DO UPDATE SET
                payload_hash=excluded.payload_hash,
                netsuite_id=excluded.netsuite_id,
                updated_at=excluded.updated_at
            """,
            (entity, key, payload_hash, netsuite_id),
        )
        self._conn.commit()

    def get_netsuite_id(self, entity: str, key: str) -> str | None:
        existing = self.get(entity, key)
        return existing[1] if existing else None
