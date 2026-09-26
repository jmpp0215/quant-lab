"""Token caching/locking for KisClient._get_token(). No live KIS calls -
the HTTP POST is faked via a stub on client._session.post.
"""
import time

import pytest

from quant.kis_client import KisApiError, KisClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KIS_TESTACC_APP_KEY", "key")
    monkeypatch.setenv("KIS_TESTACC_APP_SECRET", "secret")
    monkeypatch.setenv("KIS_TESTACC_CANO", "12345678")
    monkeypatch.setenv("KIS_TESTACC_ACNT_PRDT_CD", "01")
    monkeypatch.setattr("quant.kis_client.TOKEN_DIR", tmp_path)
    return KisClient("testacc")


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestTokenFileRoundTrip:
    def test_missing_file_returns_none(self, client):
        assert client._read_token_file() is None

    def test_write_then_read(self, client):
        client._token = "abc123"
        client._expires_at = time.time() + 3600
        client._write_token_file()

        assert client._read_token_file() == ("abc123", client._expires_at)

    def test_expired_token_returns_none(self, client):
        client._token = "abc123"
        client._expires_at = time.time() - 10
        client._write_token_file()

        assert client._read_token_file() is None

    def test_malformed_json_returns_none(self, client):
        client._token_path().parent.mkdir(parents=True, exist_ok=True)
        client._token_path().write_text("not json")

        assert client._read_token_file() is None

    def test_written_file_is_owner_only(self, client):
        client._token = "abc123"
        client._expires_at = time.time() + 3600
        client._write_token_file()

        mode = client._token_path().stat().st_mode & 0o777
        assert mode == 0o600


class TestGetTokenCaching:
    def test_reuses_in_memory_token_without_file_or_request(self, client, monkeypatch):
        client._token = "in-memory-token"
        client._expires_at = time.time() + 3600
        monkeypatch.setattr(client, "_read_token_file",
                             lambda: pytest.fail("should not touch the file cache"))

        assert client._get_token() == "in-memory-token"

    def test_reuses_valid_file_cache_without_new_request(self, client, monkeypatch):
        client._token = "cached-token"
        client._expires_at = time.time() + 3600
        client._write_token_file()
        client._token = None  # simulate a fresh process: in-memory cache empty
        client._expires_at = 0.0

        monkeypatch.setattr(
            client, "_request_new_token",
            lambda: pytest.fail("should not request a new token"))

        assert client._get_token() == "cached-token"

    def test_issues_new_token_when_no_cache_exists(self, client, monkeypatch):
        monkeypatch.setattr(client, "_request_new_token", lambda: "brand-new-token")

        assert client._get_token() == "brand-new-token"

    def test_double_checked_lock_reuses_token_written_while_waiting(self, client, monkeypatch):
        """Simulates a second process refreshing the token file in the
        window between our first (unlocked) check and acquiring the lock -
        the whole point of the file lock is that we must pick up that
        token instead of also issuing a new one."""
        original_read = client._read_token_file
        call_count = {"n": 0}

        def read_with_concurrent_write():
            call_count["n"] += 1
            if call_count["n"] == 2:
                client._token = "written-by-other-process"
                client._expires_at = time.time() + 3600
                client._write_token_file()
            return original_read()

        monkeypatch.setattr(client, "_read_token_file", read_with_concurrent_write)
        monkeypatch.setattr(
            client, "_request_new_token",
            lambda: pytest.fail("should not issue a new token"))

        assert client._get_token() == "written-by-other-process"


class TestInvalidateToken:
    def test_clears_memory_and_deletes_file(self, client):
        client._token = "abc123"
        client._expires_at = time.time() + 3600
        client._write_token_file()

        client._invalidate_token()

        assert client._token is None
        assert client._expires_at == 0.0
        assert not client._token_path().exists()

    def test_safe_when_no_file_exists(self, client):
        client._invalidate_token()

        assert client._token is None


class FakeHttpResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class TestExpiredTokenRetry:
    """EGW00123 means KIS rejected the token as expired even though our
    cache (correctly, from its own point of view) still considered it
    valid - see _invalidate_token's docstring. _request() must refresh and
    retry rather than surface the raw error."""

    def test_retries_once_with_a_fresh_token_after_egw00123(self, client, monkeypatch):
        client._token = "stale-token"
        client._expires_at = time.time() + 3600
        client._write_token_file()
        monkeypatch.setattr(client, "_request_new_token", lambda: "fresh-token")

        responses = [
            FakeHttpResponse(500, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "expired"}),
            FakeHttpResponse(200, {"rt_cd": "0", "output": {"ok": True}}),
        ]
        seen_auth_headers = []

        def fake_request(method, url, headers=None, **kwargs):
            seen_auth_headers.append(headers["Authorization"])
            return responses.pop(0)

        monkeypatch.setattr(client._session, "request", fake_request)

        result = client.get("/some/path", tr_id="TEST")

        assert result == {"rt_cd": "0", "output": {"ok": True}}
        assert seen_auth_headers == ["Bearer stale-token", "Bearer fresh-token"]

    def test_gives_up_after_max_retries_of_persistent_egw00123(self, client, monkeypatch):
        client._token = "stale-token"
        client._expires_at = time.time() + 3600
        client._write_token_file()
        monkeypatch.setattr(client, "_request_new_token", lambda: "still-stale")
        monkeypatch.setattr(
            client._session, "request",
            lambda *a, **k: FakeHttpResponse(
                500, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "expired"}))

        with pytest.raises(KisApiError):
            client.get("/some/path", tr_id="TEST")


class TestRequestNewToken:
    def test_success_caches_in_memory_and_on_disk(self, client, monkeypatch):
        monkeypatch.setattr(
            client._session, "post",
            lambda *a, **k: FakeResponse({"access_token": "fresh-token", "expires_in": 86400}))

        token = client._request_new_token()

        assert token == "fresh-token"
        assert client._token == "fresh-token"
        assert client._read_token_file()[0] == "fresh-token"

    def test_http_failure_raises_kis_api_error(self, client, monkeypatch):
        import requests

        def raise_error(*a, **k):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(client._session, "post", raise_error)

        with pytest.raises(KisApiError):
            client._request_new_token()
