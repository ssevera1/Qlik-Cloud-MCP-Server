"""Tests for the REST client against a mocked Qlik Cloud tenant."""

import httpx
import pytest

from qlik_mcp_server.auth import AuthManager
from qlik_mcp_server.config import Config
from qlik_mcp_server.qlik_cloud_client import QlikCloudClient, QlikCloudError

APP_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def _client(handler) -> QlikCloudClient:
    config = Config()
    config.qlik.tenant_url = "https://tenant.us.qlikcloud.com"
    config.qlik.api_key = "key"
    config.qlik.max_retries = 2
    return QlikCloudClient(config, AuthManager(config), transport=httpx.MockTransport(handler))


class TestSearchItems:
    async def test_maps_items_and_open_link(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"data": [{
                "id": "item1", "resourceId": APP_ID, "name": "Sales", "description": "d",
                "resourceType": "app", "spaceId": "sp1",
                "links": {"open": {"href": f"https://tenant.us.qlikcloud.com/sense/app/{APP_ID}"}},
            }]})

        items = await _client(handler).search_items("sales", resource_type="app", limit=500)

        assert seen["auth"] == "Bearer key"
        assert seen["params"]["query"] == "sales"
        assert seen["params"]["resourceType"] == "app"
        assert seen["params"]["limit"] == "100"
        assert items[0]["url"].endswith(f"/sense/app/{APP_ID}")
        assert items[0]["resource_id"] == APP_ID

    async def test_http_error_raises_without_body_leak(self):
        client = _client(lambda r: httpx.Response(403, json={"secret": "internal detail"}))
        with pytest.raises(QlikCloudError) as exc:
            await client.search_items("x")
        assert exc.value.status_code == 403
        assert "internal detail" not in str(exc.value)

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        calls = {"n": 0}
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr("qlik_mcp_server.qlik_cloud_client.asyncio.sleep", fake_sleep)

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "1"})
            return httpx.Response(200, json={"data": []})

        assert await _client(handler).search_items("x") == []
        assert calls["n"] == 2
        assert sleeps == [1]

    async def test_network_failure_becomes_qlik_cloud_error(self):
        def handler(request):
            raise httpx.ConnectError("getaddrinfo failed", request=request)

        with pytest.raises(QlikCloudError, match="Could not reach"):
            await _client(handler).search_items("x")


class TestAppEndpoints:
    async def test_get_app_returns_attributes(self):
        def handler(request):
            assert request.url.path == f"/api/v1/apps/{APP_ID}"
            return httpx.Response(200, json={"attributes": {"id": APP_ID, "name": "Sales"}})

        app = await _client(handler).get_app(APP_ID)
        assert app["name"] == "Sales"

    async def test_get_app_rejects_non_uuid(self):
        with pytest.raises(QlikCloudError, match="UUID"):
            await _client(lambda r: httpx.Response(200, json={})).get_app("../admin")

    async def test_get_app_data_metadata(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            return httpx.Response(200, json={"fields": [{"name": "Region"}], "tables": [{"name": "Sales"}]})

        meta = await _client(handler).get_app_data_metadata(APP_ID)
        assert seen["path"] == f"/api/v1/apps/{APP_ID}/data/metadata"
        assert meta["tables"][0]["name"] == "Sales"
