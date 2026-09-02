"""Tests for OAuth2 M2M token acquisition."""

import json

import httpx
import pytest

from qlik_mcp_server.auth import AuthError, AuthManager
from qlik_mcp_server.config import Config, OAuthConfig


def _oauth_config() -> Config:
    config = Config()
    config.qlik.tenant_url = "https://tenant.us.qlikcloud.com"
    config.qlik.oauth = OAuthConfig(client_id="cid", client_secret="sec")
    return config


class TestOAuthTokenRequest:
    async def test_token_request_uses_json_body(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["content_type"] = request.headers.get("content-type")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 21600})

        auth = AuthManager(_oauth_config(), transport=httpx.MockTransport(handler))

        headers = await auth.get_rest_headers()

        assert headers["Authorization"] == "Bearer tok"
        assert seen["url"] == "https://tenant.us.qlikcloud.com/oauth/token"
        assert seen["content_type"] == "application/json"
        assert seen["body"] == {
            "client_id": "cid", "client_secret": "sec", "grant_type": "client_credentials",
        }

    async def test_token_is_cached_until_expiry(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"access_token": f"tok{calls['n']}", "expires_in": 3600})

        auth = AuthManager(_oauth_config(), transport=httpx.MockTransport(handler))

        first = await auth.get_ws_headers()
        second = await auth.get_ws_headers()

        assert first == second
        assert calls["n"] == 1

    async def test_non_200_raises_auth_error(self):
        auth = AuthManager(
            _oauth_config(),
            transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "invalid_client"})),
        )
        with pytest.raises(AuthError, match="401"):
            await auth.get_rest_headers()

    async def test_token_url_outside_tenant_rejected(self):
        config = _oauth_config()
        config.qlik.oauth.token_url = "https://evil.example.com/oauth/token"
        auth = AuthManager(config, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        with pytest.raises(AuthError, match="tenant domain"):
            await auth.get_rest_headers()
