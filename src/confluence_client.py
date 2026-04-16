from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from src.models import ConfluencePage, SyncMapping

logger = logging.getLogger(__name__)


class ConfluenceClient:
    def __init__(self, base_url: str, pat: str, timeout: int = 60, retries: int = 3) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {pat}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        self._retries = retries

    def close(self) -> None:
        self._client.close()

    # ── Public API ───────────────────────────────────────────────────

    def get_pages_for_mapping(
        self, mapping: SyncMapping, since: datetime | None = None
    ) -> list[ConfluencePage]:
        cql = self._build_cql(mapping, since)
        logger.info("Fetching pages: CQL = %s", cql)
        return self._search_pages(cql)

    def get_page_content(self, page_id: str) -> ConfluencePage:
        data = self._get(
            f"/rest/api/content/{page_id}",
            params={"expand": "body.export_view,version,history,metadata.labels,space"},
        )
        return self._parse_page(data)

    # ── CQL builder ──────────────────────────────────────────────────

    def _build_cql(
        self, mapping: SyncMapping, since: datetime | None = None
    ) -> str:
        parts = [f'space = "{mapping.confluence_space_key}"', "type = page"]

        if mapping.confluence_labels_include:
            if mapping.confluence_label_mode == "all":
                for label in mapping.confluence_labels_include:
                    parts.append(f'label = "{label}"')
            else:
                labels_str = ",".join(f'"{l}"' for l in mapping.confluence_labels_include)
                parts.append(f"label IN ({labels_str})")

        if mapping.confluence_labels_exclude:
            for label in mapping.confluence_labels_exclude:
                parts.append(f'label != "{label}"')

        if since is not None:
            date_str = since.strftime("%Y-%m-%d %H:%M")
            parts.append(f'lastModified >= "{date_str}"')

        return " AND ".join(parts)

    # ── Paginated search ─────────────────────────────────────────────

    def _search_pages(self, cql: str) -> list[ConfluencePage]:
        pages: list[ConfluencePage] = []
        start = 0
        limit = 50

        while True:
            data = self._get(
                "/rest/api/content/search",
                params={
                    "cql": cql,
                    "limit": str(limit),
                    "start": str(start),
                    "expand": "body.export_view,version,history,metadata.labels,space",
                },
            )
            results = data.get("results", [])
            for item in results:
                pages.append(self._parse_page(item))

            # Check for next page
            if "_links" in data and "next" in data["_links"]:
                start += limit
            else:
                break

        logger.info("Found %d pages", len(pages))
        return pages

    # ── Response parsing ─────────────────────────────────────────────

    def _parse_page(self, data: dict) -> ConfluencePage:
        version_info = data.get("version", {})
        when = version_info.get("when", "")
        # Confluence dates: "2026-03-15T10:30:00.000+01:00"
        last_modified = self._parse_date(when)

        labels_meta = data.get("metadata", {}).get("labels", {})
        labels = [l["name"] for l in labels_meta.get("results", [])]

        space_key = data.get("space", {}).get("key", "")

        body = data.get("body", {}).get("export_view", {}).get("value", "")

        page_url = ""
        if "_links" in data:
            web_link = data["_links"].get("webui", "")
            if web_link:
                base = data["_links"].get("base", self._base_url)
                page_url = f"{base}{web_link}"

        # Author info from version (last updater) and history (creator)
        version_by = version_info.get("by", {})
        last_updated_by = self._format_user(version_by)

        history = data.get("history", {})
        created_by = self._format_user(history.get("createdBy", {}))
        created_date = history.get("createdDate", "")

        return ConfluencePage(
            id=str(data["id"]),
            title=data.get("title", ""),
            space_key=space_key,
            version=version_info.get("number", 0),
            last_modified=last_modified,
            body_storage=body,
            labels=labels,
            url=page_url,
            created_by=created_by,
            created_date=created_date,
            last_updated_by=last_updated_by,
        )

    @staticmethod
    def _format_user(user: dict) -> str:
        """Format a Confluence user dict as 'username - (Display Name)'."""
        username = user.get("username", "")
        display = user.get("displayName", "")
        if username and display:
            return f"{username} - ({display})"
        return username or display or ""

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        if not date_str:
            return datetime(1970, 1, 1)
        # Strip milliseconds and timezone for simple parsing
        clean = date_str.split(".")[0]
        try:
            return datetime.fromisoformat(clean)
        except ValueError:
            return datetime(1970, 1, 1)

    # ── HTTP with retry ──────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(1, self._retries + 1):
            try:
                resp = self._client.get(path, params=params)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", str(2**attempt)))
                    logger.warning("Rate limited, retrying in %ds (attempt %d)", wait, attempt)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError:
                if attempt == self._retries:
                    raise
                time.sleep(2**attempt)
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
                time.sleep(2**attempt)
        raise RuntimeError("Exhausted retries")  # unreachable
