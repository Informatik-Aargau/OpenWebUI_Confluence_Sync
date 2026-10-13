import httpx
import pytest

from src.openwebui_client import OpenWebUIClient


def _client() -> OpenWebUIClient:
    return OpenWebUIClient(
        base_url="https://openwebui.example.com",
        api_key="test-key",
        service_user_id="test-user",
    )


def test_wait_for_file_processing_polls_until_completed(monkeypatch):
    client = _client()
    statuses = iter(["pending", "processing", "completed"])

    def get_status(method, url, **kwargs):
        assert method == "GET"
        assert url == "/api/v1/files/file-id"
        return httpx.Response(200, json={"data": {"status": next(statuses)}})

    monkeypatch.setattr(client, "_request_with_retry", get_status)
    monkeypatch.setattr("src.openwebui_client.time.sleep", lambda _: None)

    try:
        client.wait_for_file_processing("file-id", timeout_seconds=5)
    finally:
        client.close()


def test_wait_for_file_processing_raises_with_processing_error(monkeypatch):
    client = _client()

    def get_status(method, url, **kwargs):
        return httpx.Response(
            200,
            json={"data": {"status": "failed", "error": "empty content"}},
        )

    monkeypatch.setattr(client, "_request_with_retry", get_status)

    try:
        with pytest.raises(RuntimeError, match="empty content"):
            client.wait_for_file_processing("file-id", timeout_seconds=5)
    finally:
        client.close()