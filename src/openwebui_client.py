from __future__ import annotations

import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class OpenWebUIClient:
    def __init__(
        self, base_url: str, api_key: str, service_user_id: str, timeout: int = 60,
        retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_user_id = service_user_id
        self._retries = retries
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def verify_service_user(self) -> None:
        """Verify the service user ID matches the authenticated API key.

        Raises ValueError if the API key belongs to a different user.
        """
        resp = self._client.get("/api/v1/auths/")
        self._raise_for_status(resp)
        data = resp.json()
        actual_id = data.get("id", "")
        if actual_id != self._service_user_id:
            raise ValueError(
                f"Service user ID mismatch: configured '{self._service_user_id}' "
                f"but API key belongs to '{actual_id}'. "
                f"Fix the value in rag.sync_config."
            )

    # ── Helpers ───────────────────────────────────────────────────────

    def _request_with_retry(
        self, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """Execute an HTTP request with retry on transient errors."""
        for attempt in range(1, self._retries + 1):
            try:
                resp = self._client.request(method, url, **kwargs)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", str(2 ** attempt)))
                    logger.warning("Rate limited, retrying in %ds (attempt %d)", wait, attempt)
                    time.sleep(wait)
                    continue
                return resp
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "Transient error on %s %s, retrying in %ds (attempt %d/%d)",
                    method, url, wait, attempt, self._retries,
                )
                time.sleep(wait)
        raise RuntimeError("Exhausted retries")  # unreachable

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """Like resp.raise_for_status() but logs the response body on error."""
        if resp.is_success:
            return
        try:
            body = resp.text
        except Exception:
            body = "<unreadable>"
        logger.error(
            "OpenWebUI %s %s → %d: %s",
            resp.request.method,
            resp.request.url,
            resp.status_code,
            body,
        )
        resp.raise_for_status()

    # ── Knowledge Base operations ────────────────────────────────────

    def list_knowledge_bases(self) -> list[dict]:
        """Return all knowledge bases owned by the service user."""
        items: list[dict] = []
        page = 1
        while True:
            resp = self._client.get("/api/v1/knowledge/", params={"page": str(page)})
            self._raise_for_status(resp)
            data = resp.json()
            items.extend(data.get("items", []))
            total = data.get("total", 0)
            if len(items) >= total:
                break
            page += 1
        return [
            kb for kb in items if kb.get("user_id") == self._service_user_id
        ]

    def find_knowledge_base_by_name(self, name: str) -> dict | None:
        """Search for a KB by exact name among service-user-owned KBs."""
        kbs = self.list_knowledge_bases()
        for kb in kbs:
            if kb.get("name") == name:
                return kb
        return None

    def create_knowledge_base(self, name: str, description: str = "") -> dict:
        resp = self._client.post(
            "/api/v1/knowledge/create",
            json={"name": name, "description": description},
        )
        self._raise_for_status(resp)
        kb = resp.json()
        logger.info("Created knowledge base: %s (id=%s)", name, kb.get("id"))
        return kb

    def knowledge_base_exists(self, kb_id: str) -> bool:
        """Check if a KB still exists and is owned by the service user."""
        resp = self._client.get(f"/api/v1/knowledge/{kb_id}")
        if resp.status_code == 404:
            return False
        if not resp.is_success:
            return False
        data = resp.json()
        return data.get("user_id") == self._service_user_id

    def delete_knowledge_base(self, kb_id: str) -> bool:
        """Delete a KB. Returns False if not found, not owned, or still in use."""
        if not self.knowledge_base_exists(kb_id):
            logger.debug("KB %s not found or not owned, skipping delete", kb_id)
            return False
        resp = self._client.delete(f"/api/v1/knowledge/{kb_id}/delete")
        if resp.status_code == 404:
            logger.debug("KB %s already deleted", kb_id)
            return False
        if resp.status_code >= 500:
            logger.warning(
                "Cannot delete KB %s — server returned %d. "
                "It may still be attached to a model in OpenWebUI. "
                "Remove it from all models first, then retry.",
                kb_id, resp.status_code,
            )
            return False
        self._raise_for_status(resp)
        logger.info("Deleted knowledge base %s", kb_id)
        return True

    def find_or_create_knowledge_base(
        self, name: str, description: str = ""
    ) -> str:
        """Return KB id, creating the KB if it doesn't exist."""
        existing = self.find_knowledge_base_by_name(name)
        if existing:
            kb_id = existing["id"]
            logger.debug("Found existing KB '%s' (id=%s)", name, kb_id)
            return kb_id
        kb = self.create_knowledge_base(name, description)
        return kb["id"]

    # ── File operations ──────────────────────────────────────────────

    def file_exists(self, file_id: str) -> bool:
        """Check if a file still exists in OpenWebUI and belongs to the service user."""
        resp = self._client.get(f"/api/v1/files/{file_id}")
        if resp.status_code == 404:
            return False
        if not resp.is_success:
            return False
        data = resp.json()
        return data.get("user_id") == self._service_user_id

    def upload_file(self, filename: str, content: str, metadata: dict | None = None) -> str:
        """Upload a markdown file and return its file id."""
        form_data = {}
        if metadata:
            form_data["metadata"] = json.dumps(metadata)
        resp = self._client.post(
            "/api/v1/files/",
            files={"file": (filename, content.encode("utf-8"), "text/markdown")},
            data=form_data,
        )
        self._raise_for_status(resp)
        file_data = resp.json()
        file_id = file_data["id"]
        logger.debug("Uploaded file '%s' (id=%s)", filename, file_id)
        return file_id

    def add_file_to_knowledge(self, kb_id: str, file_id: str) -> bool:
        """Add a file to a KB. Returns True on success, False on duplicate content."""
        resp = self._request_with_retry(
            "POST",
            f"/api/v1/knowledge/{kb_id}/file/add",
            json={"file_id": file_id},
        )
        if resp.status_code == 400:
            try:
                detail = str(resp.json().get("detail", ""))
            except Exception:
                detail = resp.text
            if "Duplicate content" in detail:
                logger.info(
                    "File %s already present in KB %s (duplicate content)",
                    file_id, kb_id,
                )
                return False
        self._raise_for_status(resp)
        logger.debug("Added file %s to KB %s", file_id, kb_id)
        return True

    def remove_file_from_knowledge(self, kb_id: str, file_id: str) -> bool:
        """Remove a file from a KB. Returns False if already removed / not found."""
        resp = self._client.post(
            f"/api/v1/knowledge/{kb_id}/file/remove",
            json={"file_id": file_id},
        )
        if resp.status_code in (400, 404):
            logger.debug(
                "File %s not in KB %s (already removed?), status=%d",
                file_id, kb_id, resp.status_code,
            )
            return False
        self._raise_for_status(resp)
        logger.debug("Removed file %s from KB %s", file_id, kb_id)
        return True

    def delete_file(self, file_id: str) -> bool:
        """Delete a file. Returns False if already gone or not owned by service user.

        Only deletes files belonging to the service user.
        """
        if not self.file_exists(file_id):
            logger.debug("File %s already gone or not owned, skipping delete", file_id)
            return False
        resp = self._client.delete(f"/api/v1/files/{file_id}")
        if resp.status_code == 404:
            logger.debug("File %s already deleted", file_id)
            return False
        self._raise_for_status(resp)
        logger.debug("Deleted file %s", file_id)
        return True
