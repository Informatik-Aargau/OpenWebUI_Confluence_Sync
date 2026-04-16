from __future__ import annotations

import hashlib
import logging
import re

from src.confluence_client import ConfluenceClient
from src.converter import convert_page_to_markdown
from src.models import ConfluencePage, SyncMapping, SyncState
from src.openwebui_client import OpenWebUIClient
from src.state import StateManager

logger = logging.getLogger(__name__)


class SyncOrchestrator:
    def __init__(
        self,
        confluence: ConfluenceClient,
        openwebui: OpenWebUIClient,
        state: StateManager,
        confluence_base_url: str,
        dry_run: bool = False,
        force_reupload: bool = False,
    ) -> None:
        self._confluence = confluence
        self._openwebui = openwebui
        self._state = state
        self._confluence_base_url = confluence_base_url
        self._dry_run = dry_run
        self._force_reupload = force_reupload

    def run(self, force_full: bool = False) -> None:
        """Execute sync: full or incremental based on history."""
        mappings = self._state.get_active_mappings()

        if not mappings:
            logger.warning("No active sync mappings found in database")
            return

        last_sync = self._state.get_last_successful_sync_time()
        is_full = force_full or last_sync is None
        run_type = "full" if is_full else "incremental"

        logger.info(
            "Starting %s sync (%d mappings)%s",
            run_type,
            len(mappings),
            " [DRY RUN]" if self._dry_run else "",
        )

        run_id = self._state.create_sync_run(run_type)
        total_synced = 0
        total_failed = 0

        for mapping in mappings:
            synced, failed = self._sync_mapping(mapping, is_full, last_sync)
            total_synced += synced
            total_failed += failed

        # Clean up KBs no longer referenced by any mapping
        self._cleanup_stale_knowledge_bases()

        self._state.finish_sync_run(run_id, total_synced, total_failed)
        logger.info(
            "Sync finished: %d synced, %d failed",
            total_synced,
            total_failed,
        )

    def _sync_mapping(
        self,
        mapping: SyncMapping,
        is_full: bool,
        last_sync: object,
    ) -> tuple[int, int]:
        """Sync a single mapping. Returns (synced_count, failed_count)."""
        logger.info(
            "Processing mapping #%d: space=%s include=%s exclude=%s → KB '%s'",
            mapping.id,
            mapping.confluence_space_key,
            mapping.confluence_labels_include or "(all)",
            mapping.confluence_labels_exclude or "(none)",
            mapping.openwebui_knowledge_name,
        )

        # Force full sync for mappings with label filters — Confluence
        # does not update lastModified when labels change, so incremental
        # would miss pages that gained or lost a label.
        has_labels = bool(mapping.confluence_labels_include or mapping.confluence_labels_exclude)
        if has_labels and not is_full:
            logger.info(
                "Mapping #%d uses label filters, forcing full sync",
                mapping.id,
            )
            is_full = True

        # Ensure KB exists
        kb_id = self._ensure_knowledge_base(mapping)

        # Fetch pages from Confluence
        since = None if is_full else last_sync
        pages = self._confluence.get_pages_for_mapping(mapping, since)

        synced = 0
        failed = 0

        for page in pages:
            try:
                changed = self._sync_page(page, mapping, kb_id)
                if changed:
                    synced += 1
            except Exception:
                logger.exception(
                    "Failed to sync page '%s' (id=%s)", page.title, page.id
                )
                failed += 1

        # Orphan cleanup only on full sync
        if is_full:
            current_ids = {p.id for p in pages}
            orphans = self._state.get_orphaned_pages(mapping.id, current_ids)
            for orphan in orphans:
                try:
                    self._remove_orphan(orphan)
                except Exception:
                    logger.exception(
                        "Failed to remove orphan page id=%s",
                        orphan.confluence_page_id,
                    )
                    failed += 1

        # Clean up empty KB after orphan removal
        if is_full:
            self._cleanup_empty_kb(mapping)

        logger.info(
            "Mapping #%d done: %d synced, %d failed",
            mapping.id,
            synced,
            failed,
        )
        return synced, failed

    def _ensure_knowledge_base(self, mapping: SyncMapping) -> str:
        """Ensure the OpenWebUI KB exists. Self-heals if KB was manually deleted."""
        if mapping.openwebui_kb_id:
            # Verify it still exists (self-healing)
            if self._openwebui.knowledge_base_exists(mapping.openwebui_kb_id):
                return mapping.openwebui_kb_id
            logger.warning(
                "KB %s for mapping #%d was deleted externally, recreating",
                mapping.openwebui_kb_id, mapping.id,
            )
            mapping.openwebui_kb_id = None

        if self._dry_run:
            logger.info("[DRY RUN] Would create KB '%s'", mapping.openwebui_knowledge_name)
            return "dry-run-kb-id"

        kb_id = self._openwebui.find_or_create_knowledge_base(
            mapping.openwebui_knowledge_name,
            mapping.openwebui_knowledge_description or "",
        )
        self._state.update_mapping_kb_id(mapping.id, kb_id)
        mapping.openwebui_kb_id = kb_id

        # Re-link existing files to the new KB (self-healing after external deletion)
        self._relink_files_to_kb(mapping.id, kb_id)

        return kb_id

    def _relink_files_to_kb(self, mapping_id: int, kb_id: str) -> None:
        """Re-link all tracked files for a mapping to a (re)created KB.

        Files that no longer exist are removed from state so they get
        re-uploaded on the next sync pass.
        """
        states = self._state.get_all_states_for_mapping(mapping_id)
        if not states:
            return

        logger.info(
            "Re-linking %d files to KB %s for mapping #%d",
            len(states), kb_id, mapping_id,
        )
        for i, s in enumerate(states, 1):
            if not self._openwebui.file_exists(s.openwebui_file_id):
                logger.warning(
                    "File %s for page '%s' no longer exists, clearing state",
                    s.openwebui_file_id, s.confluence_title,
                )
                self._state.delete_sync_state(s.confluence_page_id, mapping_id)
                continue
            self._openwebui.add_file_to_knowledge(kb_id, s.openwebui_file_id)
            logger.info(
                "Linked file %d/%d '%s' to KB %s",
                i, len(states), s.confluence_title, kb_id,
            )

        # Update the KB ID in all remaining state entries
        self._state.update_kb_id_for_mapping(mapping_id, kb_id)

    def _sync_page(
        self, page: ConfluencePage, mapping: SyncMapping, kb_id: str
    ) -> bool:
        """Sync a single page. Returns True if content was changed/added."""
        existing = self._state.get_synced_page(page.id, mapping.id)

        # Fast path: skip expensive conversion if version hasn't changed
        if existing and existing.confluence_version == page.version and not self._force_reupload:
            if self._openwebui.file_exists(existing.openwebui_file_id):
                logger.debug("Skipping unchanged page '%s' (id=%s, version=%d)", page.title, page.id, page.version)
                return False
            logger.warning(
                "File %s for page '%s' was deleted externally, re-uploading",
                existing.openwebui_file_id, page.title,
            )
            self._state.delete_sync_state(page.id, mapping.id)
            existing = None

        markdown = convert_page_to_markdown(page, self._confluence_base_url)
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()

        if existing and existing.content_hash == content_hash and not self._force_reupload:
            # Content unchanged — but verify the file still exists (self-healing)
            if self._openwebui.file_exists(existing.openwebui_file_id):
                logger.debug("Skipping unchanged page '%s' (id=%s)", page.title, page.id)
                return False
            # File was deleted externally → clear state and re-sync
            logger.warning(
                "File %s for page '%s' was deleted externally, re-uploading",
                existing.openwebui_file_id, page.title,
            )
            self._state.delete_sync_state(page.id, mapping.id)
            existing = None

        filename = _build_filename(page)

        if self._dry_run:
            action = "update" if existing else "create"
            logger.info(
                "[DRY RUN] Would %s: '%s' (%d bytes)",
                action,
                page.title,
                len(markdown),
            )
            return True

        if existing:
            # Content changed — remove old file from KB and delete it
            logger.info("Updating page '%s' (id=%s)", page.title, page.id)
            self._openwebui.remove_file_from_knowledge(
                existing.openwebui_kb_id, existing.openwebui_file_id
            )
            self._openwebui.delete_file(existing.openwebui_file_id)
        else:
            logger.info("Creating page '%s' (id=%s)", page.title, page.id)

        # Upload a fresh file for this mapping
        metadata = {
            "id": page.id,
            "title": page.title,
            "Link": page.url,
            "createdBy": page.created_by,
            "createdDate": page.created_date,
            "lastUpdatedBy": page.last_updated_by,
            "lastUpdatedWhen": page.last_modified.isoformat(),
            "versionNumber": page.version,
            "file": f"{page.id}_{page.title.replace(' ', '_')}",
        }
        file_id = self._openwebui.upload_file(filename, markdown, metadata=metadata)
        added = self._openwebui.add_file_to_knowledge(kb_id, file_id)

        if not added:
            # Duplicate content — another mapping already has a file with
            # the same content.  Delete the just-uploaded duplicate and
            # link the existing file instead.
            self._openwebui.delete_file(file_id)

            other = self._state.get_existing_file_for_page(page.id)
            if other and self._openwebui.file_exists(other.openwebui_file_id):
                file_id = other.openwebui_file_id
                logger.info(
                    "Reusing file %s from mapping #%d for page '%s'",
                    file_id, other.mapping_id, page.title,
                )
                # Link to KB — may return False if already linked, that's ok
                self._openwebui.add_file_to_knowledge(kb_id, file_id)
            else:
                logger.warning(
                    "Duplicate content for page '%s' but no reusable file found",
                    page.title,
                )

        state = SyncState(
            confluence_page_id=page.id,
            confluence_space_key=page.space_key,
            confluence_version=page.version,
            confluence_title=page.title,
            confluence_last_modified=page.last_modified,
            openwebui_file_id=file_id,
            openwebui_kb_id=kb_id,
            mapping_id=mapping.id,
            content_hash=content_hash,
        )
        self._state.upsert_sync_state(state)
        return True

    def _cleanup_stale_knowledge_bases(self) -> None:
        """Delete KBs owned by the service user that are no longer in any mapping."""
        known_kb_ids = self._state.get_all_mapping_kb_ids()
        remote_kbs = self._openwebui.list_knowledge_bases()

        for kb in remote_kbs:
            kb_id = kb["id"]
            if kb_id in known_kb_ids:
                continue

            name = kb.get("name", kb_id)
            if self._dry_run:
                logger.info(
                    "[DRY RUN] Would delete stale KB '%s' (id=%s)", name, kb_id,
                )
                continue

            logger.info(
                "Deleting stale KB '%s' (id=%s) — no mapping references it",
                name, kb_id,
            )
            self._openwebui.delete_knowledge_base(kb_id)

    def _cleanup_empty_kb(self, mapping: SyncMapping) -> None:
        """Delete the KB from OpenWebUI if no files remain for this mapping."""
        if not mapping.openwebui_kb_id:
            return
        remaining = self._state.get_all_states_for_mapping(mapping.id)
        if remaining:
            return

        if self._dry_run:
            logger.info(
                "[DRY RUN] Would delete empty KB '%s' (id=%s)",
                mapping.openwebui_knowledge_name, mapping.openwebui_kb_id,
            )
            return

        logger.info(
            "KB '%s' (id=%s) has no files left, deleting",
            mapping.openwebui_knowledge_name, mapping.openwebui_kb_id,
        )
        self._openwebui.delete_knowledge_base(mapping.openwebui_kb_id)
        self._state.update_mapping_kb_id(mapping.id, None)
        mapping.openwebui_kb_id = None

    def _remove_orphan(self, orphan: SyncState) -> None:
        """Remove a page that no longer matches this mapping's criteria.

        Unlinks the file from the KB and deletes it (each mapping owns
        its own file).
        """
        logger.info(
            "Removing orphan: '%s' (page_id=%s) from KB %s",
            orphan.confluence_title,
            orphan.confluence_page_id,
            orphan.openwebui_kb_id,
        )
        if self._dry_run:
            logger.info("[DRY RUN] Would remove orphan '%s'", orphan.confluence_title)
            return

        # Unlink from KB (tolerates 404/400 if already removed)
        self._openwebui.remove_file_from_knowledge(
            orphan.openwebui_kb_id, orphan.openwebui_file_id
        )

        # Remove state entry first (so count_mappings_for_file is accurate)
        self._state.delete_sync_state(orphan.confluence_page_id, orphan.mapping_id)

        # Delete file only if no other mapping still references it
        remaining = self._state.count_mappings_for_file(orphan.openwebui_file_id)
        if remaining == 0:
            self._openwebui.delete_file(orphan.openwebui_file_id)
        else:
            logger.debug(
                "File %s still used by %d other mapping(s), keeping",
                orphan.openwebui_file_id, remaining,
            )


def _build_filename(page: ConfluencePage) -> str:
    """Build a safe filename: {space}_{page_id}_{sanitized_title}.md"""
    safe_title = re.sub(r"[^\w\s-]", "", page.title)
    safe_title = re.sub(r"[\s]+", "_", safe_title).strip("_")
    safe_title = safe_title[:80]  # limit length
    return f"{page.space_key}_{page.id}_{safe_title}.md"
