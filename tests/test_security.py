"""Security-focused tests: identifier validation, SSRF guards, secret hygiene."""

import httpx
import pytest

from qlik_mcp_server.auth import AuthError, AuthManager
from qlik_mcp_server.config import Config, OAuthConfig
from qlik_mcp_server.engine_client import EngineError, _validate_id
from qlik_mcp_server.server import create_server

from .fakes import FakeWebSocket

APP_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


class TestIdentifierValidation:
    def test_rejects_uuid_with_trailing_newline(self):
        # re.match with "$" would accept "uuid\n"; the URL must never carry it.
        with pytest.raises(EngineError):
            _validate_id(APP_ID + "\n", "app_id")

    def test_rejects_uuid_with_query_suffix(self):
        with pytest.raises(EngineError):
            _validate_id(APP_ID + "?x=1", "app_id")

    def test_rejects_uuid_with_unicode_lookalike(self):
        with pytest.raises(EngineError):
            _validate_id(APP_ID.replace("a", "а", 1), "app_id")


class TestTenantUrlHardening:
    def _cfg(self, url: str) -> Config:
        config = Config()
        config.qlik.tenant_url = url
        config.qlik.api_key = "k"
        return config

    def test_tenant_host_ignores_path_and_trailing_slash(self):
        assert self._cfg("https://t.us.qlikcloud.com/").tenant_host == "t.us.qlikcloud.com"
        assert self._cfg("https://t.us.qlikcloud.com/some/path").tenant_host == "t.us.qlikcloud.com"

    def test_validate_rejects_userinfo_in_tenant_url(self):
        errors = self._cfg("https://user:pw@evil.example.com").validate()
        assert any("tenant_url" in e for e in errors)

    def test_validate_rejects_path_query_or_fragment(self):
        for url in (
            "https://t.us.qlikcloud.com/api",
            "https://t.us.qlikcloud.com/?x=1",
            "https://t.us.qlikcloud.com/#frag",
        ):
            errors = self._cfg(url).validate()
            assert any("tenant_url" in e for e in errors), url

    def test_validate_accepts_plain_host_with_trailing_slash(self):
        assert self._cfg("https://t.us.qlikcloud.com/").validate() == []


class TestOAuthTokenUrlGuard:
    def _auth(self, token_url: str) -> AuthManager:
        config = Config()
        config.qlik.tenant_url = "https://tenant.us.qlikcloud.com"
        config.qlik.oauth = OAuthConfig(client_id="c", client_secret="s", token_url=token_url)
        return AuthManager(config, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"access_token": "t"})))

    async def test_lookalike_suffix_domain_rejected(self):
        with pytest.raises(AuthError, match="tenant domain"):
            await self._auth("https://tenant.us.qlikcloud.com.evil.example/oauth/token").get_rest_headers()

    async def test_http_scheme_rejected(self):
        with pytest.raises(AuthError, match="HTTPS"):
            await self._auth("http://tenant.us.qlikcloud.com/oauth/token").get_rest_headers()

    async def test_userinfo_trick_rejected(self):
        with pytest.raises(AuthError, match="tenant domain"):
            await self._auth("https://tenant.us.qlikcloud.com@evil.example/oauth/token").get_rest_headers()

    async def test_subdomain_of_tenant_allowed(self):
        headers = await self._auth("https://auth.tenant.us.qlikcloud.com/oauth/token").get_rest_headers()
        assert headers["Authorization"] == "Bearer t"


class TestErrorSanitization:
    async def test_unexpected_exception_does_not_leak_details(self):
        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "super-secret-key"

        class Boom:
            def __init__(self):
                self.config = config

            def open_app(self, app_id):
                raise RuntimeError("stack detail with super-secret-key inside")

        server = create_server(config, qlik_client=object(), engine_client=Boom())
        result = await server.call_tool("qlik_get_fields", {"app_id": APP_ID})

        payload = result.structured_content
        assert "error" in payload
        assert "super-secret-key" not in str(payload)
        assert "stack detail" not in str(payload)

    async def test_engine_error_message_is_truncated(self):
        ws = FakeWebSocket(lambda m: {"error": {"code": 1, "message": "x" * 5000}})
        from qlik_mcp_server.engine_client import EngineSession

        session = EngineSession(ws, doc_handle=1, app_id="app")
        with pytest.raises(EngineError) as exc:
            await session._send("DoSave", 1)
        assert len(str(exc.value)) <= 600


class TestHttpTransportSecurity:
    def test_loopback_bind_enables_dns_rebinding_protection(self):
        from qlik_mcp_server.server import transport_security_for

        config = Config()
        config.server.http_host = "127.0.0.1"
        config.server.http_port = 8080
        settings = transport_security_for(config)
        assert settings is not None
        assert settings.enable_dns_rebinding_protection is True
        assert "127.0.0.1:8080" in settings.allowed_hosts
        assert "localhost:8080" in settings.allowed_hosts

    def test_public_bind_leaves_host_checks_to_proxy(self):
        from qlik_mcp_server.server import transport_security_for

        config = Config()
        config.server.http_host = "0.0.0.0"  # noqa: S104 - exercising the non-loopback branch
        assert transport_security_for(config) is None
