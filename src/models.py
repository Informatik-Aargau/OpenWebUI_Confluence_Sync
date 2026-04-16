from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SyncMapping:
    id: int
    confluence_space_key: str
    openwebui_knowledge_name: str
    openwebui_knowledge_description: str | None = None
    confluence_labels_include: list[str] = field(default_factory=list)
    confluence_labels_exclude: list[str] = field(default_factory=list)
    confluence_label_mode: str = "any"  # 'any' or 'all'
    openwebui_kb_id: str | None = None
    enabled: bool = True


@dataclass
class SyncState:
    confluence_page_id: str
    confluence_space_key: str
    confluence_version: int
    confluence_title: str
    confluence_last_modified: datetime
    openwebui_file_id: str
    openwebui_kb_id: str
    mapping_id: int
    content_hash: str
    synced_at: datetime | None = None


@dataclass
class SyncRun:
    id: int | None = None
    run_type: str = "incremental"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    pages_synced: int = 0
    pages_failed: int = 0
    status: str = "running"


@dataclass
class ConfluencePage:
    """Represents a Confluence page with its content and metadata."""

    id: str
    title: str
    space_key: str
    version: int
    last_modified: datetime
    body_storage: str
    labels: list[str] = field(default_factory=list)
    url: str = ""
    created_by: str = ""
    created_date: str = ""
    last_updated_by: str = ""
