from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, call

import pytest

from src.models import SyncState
from src.sync import SyncOrchestrator


def _make_state(
    page_id: str = "1",
    file_id: str = "file-1",
    kb_id: str = "kb-1",
    mapping_id: int = 99,
    title: str = "Orphan Page",
) -> SyncState:
    return SyncState(
        confluence_page_id=page_id,
        confluence_space_key="TST",
        confluence_version=1,
        confluence_title=title,
        confluence_last_modified=datetime(2025, 1, 1),
        openwebui_file_id=file_id,
        openwebui_kb_id=kb_id,
        mapping_id=mapping_id,
        content_hash="abc123",
    )


def _make_orchestrator(dry_run: bool = False) -> SyncOrchestrator:
    return SyncOrchestrator(
        confluence=MagicMock(),
        openwebui=MagicMock(),
        state=MagicMock(),
        confluence_base_url="https://confluence.example.com",
        dry_run=dry_run,
    )


class TestCleanupRemovedMappings:
    def test_no_orphans_does_nothing(self):
        orch = _make_orchestrator()
        orch._state.get_states_for_removed_mappings.return_value = []

        orch._cleanup_removed_mappings({1, 2})

        orch._openwebui.delete_file.assert_not_called()
        orch._state.delete_states_for_removed_mappings.assert_not_called()

    def test_deletes_orphaned_file_and_state(self):
        orch = _make_orchestrator()
        orphan = _make_state(page_id="10", file_id="file-10", kb_id="kb-old", mapping_id=99)
        orch._state.get_states_for_removed_mappings.return_value = [orphan]
        orch._state.count_mappings_for_file.return_value = 1  # only the orphan

        orch._cleanup_removed_mappings({1, 2})

        orch._openwebui.remove_file_from_knowledge.assert_called_once_with("kb-old", "file-10")
        orch._openwebui.delete_file.assert_called_once_with("file-10")
        orch._state.delete_states_for_removed_mappings.assert_called_once_with({1, 2})

    def test_keeps_file_if_active_mapping_uses_it(self):
        orch = _make_orchestrator()
        orphan = _make_state(page_id="10", file_id="shared-file", mapping_id=99)
        orch._state.get_states_for_removed_mappings.return_value = [orphan]
        # 2 total refs, 1 orphaned → 1 active ref remains
        orch._state.count_mappings_for_file.return_value = 2

        orch._cleanup_removed_mappings({1})

        orch._openwebui.delete_file.assert_not_called()
        orch._state.delete_states_for_removed_mappings.assert_called_once_with({1})

    def test_multiple_orphans_same_file_deleted_once(self):
        orch = _make_orchestrator()
        orphan1 = _make_state(page_id="10", file_id="file-x", mapping_id=90)
        orphan2 = _make_state(page_id="11", file_id="file-x", mapping_id=91)
        orch._state.get_states_for_removed_mappings.return_value = [orphan1, orphan2]
        # 2 refs total, both orphaned → 0 active
        orch._state.count_mappings_for_file.return_value = 2

        orch._cleanup_removed_mappings({1})

        orch._openwebui.delete_file.assert_called_once_with("file-x")

    def test_multiple_orphans_different_files(self):
        orch = _make_orchestrator()
        orphan1 = _make_state(page_id="10", file_id="file-a", mapping_id=99)
        orphan2 = _make_state(page_id="11", file_id="file-b", mapping_id=99)
        orch._state.get_states_for_removed_mappings.return_value = [orphan1, orphan2]
        orch._state.count_mappings_for_file.return_value = 1

        orch._cleanup_removed_mappings({1})

        assert orch._openwebui.delete_file.call_count == 2
        orch._openwebui.delete_file.assert_any_call("file-a")
        orch._openwebui.delete_file.assert_any_call("file-b")

    def test_dry_run_does_not_delete(self):
        orch = _make_orchestrator(dry_run=True)
        orphan = _make_state(page_id="10", file_id="file-10", mapping_id=99)
        orch._state.get_states_for_removed_mappings.return_value = [orphan]
        orch._state.count_mappings_for_file.return_value = 1

        orch._cleanup_removed_mappings({1})

        orch._openwebui.delete_file.assert_not_called()
        orch._openwebui.remove_file_from_knowledge.assert_not_called()
        orch._state.delete_states_for_removed_mappings.assert_not_called()

    def test_orphan_without_kb_id_skips_unlink(self):
        orch = _make_orchestrator()
        orphan = _make_state(page_id="10", file_id="file-10", kb_id="", mapping_id=99)
        orch._state.get_states_for_removed_mappings.return_value = [orphan]
        orch._state.count_mappings_for_file.return_value = 1

        orch._cleanup_removed_mappings({1})

        orch._openwebui.remove_file_from_knowledge.assert_not_called()
        orch._openwebui.delete_file.assert_called_once_with("file-10")

    def test_empty_active_ids_treats_all_as_orphaned(self):
        orch = _make_orchestrator()
        orphan = _make_state(page_id="10", file_id="file-10", mapping_id=99)
        orch._state.get_states_for_removed_mappings.return_value = [orphan]
        orch._state.count_mappings_for_file.return_value = 1

        orch._cleanup_removed_mappings(set())

        orch._openwebui.delete_file.assert_called_once_with("file-10")
        orch._state.delete_states_for_removed_mappings.assert_called_once_with(set())
