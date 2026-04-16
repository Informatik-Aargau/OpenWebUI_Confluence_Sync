from __future__ import annotations

import logging
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from src.models import SyncMapping, SyncRun, SyncState

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE IF NOT EXISTS rag.sync_mappings (
    id                              SERIAL PRIMARY KEY,
    confluence_space_key            VARCHAR(64) NOT NULL,
    confluence_labels_include       TEXT[],
    confluence_labels_exclude       TEXT[],
    confluence_label_mode           VARCHAR(8) DEFAULT 'any',
    openwebui_knowledge_name        TEXT NOT NULL,
    openwebui_knowledge_description TEXT,
    openwebui_kb_id                 VARCHAR(128),
    enabled                         BOOLEAN DEFAULT TRUE,
    created_at                      TIMESTAMP DEFAULT NOW(),
    updated_at                      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rag.sync_state (
    confluence_page_id       VARCHAR(64) NOT NULL,
    confluence_space_key     VARCHAR(64),
    confluence_version       INTEGER,
    confluence_title         TEXT,
    confluence_last_modified TIMESTAMP,
    openwebui_file_id        VARCHAR(128),
    openwebui_kb_id          VARCHAR(128),
    mapping_id               INTEGER NOT NULL REFERENCES rag.sync_mappings(id),
    synced_at                TIMESTAMP DEFAULT NOW(),
    content_hash             VARCHAR(64),
    PRIMARY KEY (confluence_page_id, mapping_id)
);

CREATE TABLE IF NOT EXISTS rag.sync_runs (
    id           SERIAL PRIMARY KEY,
    run_type     VARCHAR(16),
    started_at   TIMESTAMP,
    finished_at  TIMESTAMP,
    pages_synced INTEGER DEFAULT 0,
    pages_failed INTEGER DEFAULT 0,
    status       VARCHAR(16) DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS rag.sync_config (
    key   VARCHAR(64) PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StateManager:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._conn: psycopg2.extensions.connection | None = None

    def connect(self) -> None:
        self._conn = psycopg2.connect(self._database_url)
        self._conn.autocommit = False

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()

    @property
    def conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self.connect()
        assert self._conn is not None
        return self._conn

    def ensure_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        self.conn.commit()
        logger.info("Database schema ensured")

    # ── Config queries ────────────────────────────────────────────────

    def get_config(self, key: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM rag.sync_config WHERE key = %s", (key,)
            )
            row = cur.fetchone()
        return row[0] if row else None

    def get_service_user_id(self) -> str:
        """Return the configured OpenWebUI service user ID.

        Raises ValueError if not configured.
        """
        user_id = self.get_config("openwebui_service_user_id")
        if not user_id:
            raise ValueError(
                "openwebui_service_user_id not set in rag.sync_config. "
                "INSERT INTO rag.sync_config (key, value) VALUES "
                "('openwebui_service_user_id', '<user-id>');")
        return user_id

    # ── Mapping queries ──────────────────────────────────────────────

    def get_active_mappings(self) -> list[SyncMapping]:
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM rag.sync_mappings WHERE enabled = TRUE ORDER BY id"
            )
            rows = cur.fetchall()
        return [
            SyncMapping(
                id=r["id"],
                confluence_space_key=r["confluence_space_key"],
                confluence_labels_include=r["confluence_labels_include"] or [],
                confluence_labels_exclude=r["confluence_labels_exclude"] or [],
                confluence_label_mode=r["confluence_label_mode"] or "any",
                openwebui_knowledge_name=r["openwebui_knowledge_name"],
                openwebui_knowledge_description=r["openwebui_knowledge_description"],
                openwebui_kb_id=r["openwebui_kb_id"],
                enabled=r["enabled"],
            )
            for r in rows
        ]

    def get_all_mapping_kb_ids(self) -> set[str]:
        """Return all openwebui_kb_id values from ALL mappings (enabled + disabled)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT openwebui_kb_id FROM rag.sync_mappings WHERE openwebui_kb_id IS NOT NULL"
            )
            rows = cur.fetchall()
        return {r[0] for r in rows}

    def update_mapping_kb_id(self, mapping_id: int, kb_id: str | None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE rag.sync_mappings SET openwebui_kb_id = %s, updated_at = NOW() WHERE id = %s",
                (kb_id, mapping_id),
            )
        self.conn.commit()

    # ── Sync state queries ───────────────────────────────────────────

    def get_synced_page(self, page_id: str, mapping_id: int) -> SyncState | None:
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM rag.sync_state WHERE confluence_page_id = %s AND mapping_id = %s",
                (page_id, mapping_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return SyncState(
            confluence_page_id=row["confluence_page_id"],
            confluence_space_key=row["confluence_space_key"],
            confluence_version=row["confluence_version"],
            confluence_title=row["confluence_title"],
            confluence_last_modified=row["confluence_last_modified"],
            openwebui_file_id=row["openwebui_file_id"],
            openwebui_kb_id=row["openwebui_kb_id"],
            mapping_id=row["mapping_id"],
            content_hash=row["content_hash"],
            synced_at=row["synced_at"],
        )

    def get_existing_file_for_page(self, page_id: str) -> SyncState | None:
        """Return any existing sync_state row for this page (regardless of mapping).

        Used to find a file that was already uploaded for this page so it
        can be linked to additional KBs without re-uploading.
        """
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM rag.sync_state WHERE confluence_page_id = %s LIMIT 1",
                (page_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return SyncState(
            confluence_page_id=row["confluence_page_id"],
            confluence_space_key=row["confluence_space_key"],
            confluence_version=row["confluence_version"],
            confluence_title=row["confluence_title"],
            confluence_last_modified=row["confluence_last_modified"],
            openwebui_file_id=row["openwebui_file_id"],
            openwebui_kb_id=row["openwebui_kb_id"],
            mapping_id=row["mapping_id"],
            content_hash=row["content_hash"],
            synced_at=row["synced_at"],
        )

    def count_mappings_for_file(self, file_id: str) -> int:
        """Return how many mappings still reference a given OpenWebUI file_id."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM rag.sync_state WHERE openwebui_file_id = %s",
                (file_id,),
            )
            return cur.fetchone()[0]  # type: ignore[index]

    def upsert_sync_state(self, state: SyncState) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag.sync_state (
                    confluence_page_id, confluence_space_key, confluence_version,
                    confluence_title, confluence_last_modified,
                    openwebui_file_id, openwebui_kb_id, mapping_id,
                    content_hash, synced_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (confluence_page_id, mapping_id) DO UPDATE SET
                    confluence_space_key = EXCLUDED.confluence_space_key,
                    confluence_version = EXCLUDED.confluence_version,
                    confluence_title = EXCLUDED.confluence_title,
                    confluence_last_modified = EXCLUDED.confluence_last_modified,
                    openwebui_file_id = EXCLUDED.openwebui_file_id,
                    openwebui_kb_id = EXCLUDED.openwebui_kb_id,
                    mapping_id = EXCLUDED.mapping_id,
                    content_hash = EXCLUDED.content_hash,
                    synced_at = NOW()
                """,
                (
                    state.confluence_page_id,
                    state.confluence_space_key,
                    state.confluence_version,
                    state.confluence_title,
                    state.confluence_last_modified,
                    state.openwebui_file_id,
                    state.openwebui_kb_id,
                    state.mapping_id,
                    state.content_hash,
                ),
            )
        self.conn.commit()

    def delete_sync_state(self, page_id: str, mapping_id: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rag.sync_state WHERE confluence_page_id = %s AND mapping_id = %s",
                (page_id, mapping_id),
            )
        self.conn.commit()

    def get_all_states_for_mapping(self, mapping_id: int) -> list[SyncState]:
        """Return all sync_state entries for a given mapping."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM rag.sync_state WHERE mapping_id = %s",
                (mapping_id,),
            )
            rows = cur.fetchall()
        return [
            SyncState(
                confluence_page_id=r["confluence_page_id"],
                confluence_space_key=r["confluence_space_key"],
                confluence_version=r["confluence_version"],
                confluence_title=r["confluence_title"],
                confluence_last_modified=r["confluence_last_modified"],
                openwebui_file_id=r["openwebui_file_id"],
                openwebui_kb_id=r["openwebui_kb_id"],
                mapping_id=r["mapping_id"],
                content_hash=r["content_hash"],
                synced_at=r["synced_at"],
            )
            for r in rows
        ]

    def update_kb_id_for_mapping(self, mapping_id: int, new_kb_id: str) -> int:
        """Update openwebui_kb_id for all sync_state rows of a mapping. Returns rows affected."""
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE rag.sync_state SET openwebui_kb_id = %s WHERE mapping_id = %s",
                (new_kb_id, mapping_id),
            )
            count = cur.rowcount
        self.conn.commit()
        return count

    def get_orphaned_pages(
        self, mapping_id: int, current_page_ids: set[str]
    ) -> list[SyncState]:
        """Find pages in sync_state that no longer exist in Confluence for a given mapping."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM rag.sync_state WHERE mapping_id = %s",
                (mapping_id,),
            )
            rows = cur.fetchall()
        return [
            SyncState(
                confluence_page_id=r["confluence_page_id"],
                confluence_space_key=r["confluence_space_key"],
                confluence_version=r["confluence_version"],
                confluence_title=r["confluence_title"],
                confluence_last_modified=r["confluence_last_modified"],
                openwebui_file_id=r["openwebui_file_id"],
                openwebui_kb_id=r["openwebui_kb_id"],
                mapping_id=r["mapping_id"],
                content_hash=r["content_hash"],
                synced_at=r["synced_at"],
            )
            for r in rows
            if r["confluence_page_id"] not in current_page_ids
        ]

    # ── Orphaned mapping cleanup ─────────────────────────────────────

    def get_states_for_removed_mappings(self, active_mapping_ids: set[int]) -> list[SyncState]:
        """Return sync_state entries whose mapping_id is not in the active set.

        This catches states left behind when a mapping was deleted or disabled.
        """
        if not active_mapping_ids:
            where = "1=1"
            params: tuple = ()  # type: ignore[assignment]
        else:
            placeholders = ",".join(["%s"] * len(active_mapping_ids))
            where = f"mapping_id NOT IN ({placeholders})"
            params = tuple(active_mapping_ids)

        with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(f"SELECT * FROM rag.sync_state WHERE {where}", params)
            rows = cur.fetchall()
        return [
            SyncState(
                confluence_page_id=r["confluence_page_id"],
                confluence_space_key=r["confluence_space_key"],
                confluence_version=r["confluence_version"],
                confluence_title=r["confluence_title"],
                confluence_last_modified=r["confluence_last_modified"],
                openwebui_file_id=r["openwebui_file_id"],
                openwebui_kb_id=r["openwebui_kb_id"],
                mapping_id=r["mapping_id"],
                content_hash=r["content_hash"],
                synced_at=r["synced_at"],
            )
            for r in rows
        ]

    def delete_states_for_removed_mappings(self, active_mapping_ids: set[int]) -> int:
        """Delete sync_state entries not belonging to any active mapping. Returns rows deleted."""
        if not active_mapping_ids:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM rag.sync_state")
                count = cur.rowcount
        else:
            placeholders = ",".join(["%s"] * len(active_mapping_ids))
            with self.conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM rag.sync_state WHERE mapping_id NOT IN ({placeholders})",
                    tuple(active_mapping_ids),
                )
                count = cur.rowcount
        self.conn.commit()
        return count

    # ── Sync run tracking ────────────────────────────────────────────

    def get_last_successful_sync_time(self) -> datetime | None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT finished_at FROM rag.sync_runs
                WHERE status IN ('success', 'partial')
                ORDER BY finished_at DESC LIMIT 1
                """
            )
            row = cur.fetchone()
        if row is None:
            return None
        return row[0]

    def create_sync_run(self, run_type: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rag.sync_runs (run_type, started_at, status) VALUES (%s, %s, 'running') RETURNING id",
                (run_type, datetime.now(timezone.utc)),
            )
            run_id: int = cur.fetchone()[0]  # type: ignore[index]
        self.conn.commit()
        return run_id

    def finish_sync_run(
        self, run_id: int, pages_synced: int, pages_failed: int
    ) -> None:
        status = "success" if pages_failed == 0 else "partial" if pages_synced > 0 else "failed"
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rag.sync_runs
                SET finished_at = %s, pages_synced = %s, pages_failed = %s, status = %s
                WHERE id = %s
                """,
                (datetime.now(timezone.utc), pages_synced, pages_failed, status, run_id),
            )
        self.conn.commit()
