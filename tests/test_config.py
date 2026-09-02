"""Tests for configuration management."""

import pytest

from qlik_mcp_server.config import Config, _resolve_env_vars


class TestEnvVarResolution:
    def test_resolves_existing_var(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "my-api-key")
        assert _resolve_env_vars("${TEST_KEY}") == "my-api-key"

    def test_leaves_missing_var(self):
        result = _resolve_env_vars("${NONEXISTENT_VAR_XYZ}")
        assert result == "${NONEXISTENT_VAR_XYZ}"

    def test_plain_string_unchanged(self):
        assert _resolve_env_vars("https://tenant.qlikcloud.com") == "https://tenant.qlikcloud.com"


class TestConfigLoad:
    def test_load_minimal(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "qlik:\n  tenant_url: https://test.us.qlikcloud.com\n  api_key: test-key\n"
        )

        config = Config.load(config_file)
        assert config.qlik.tenant_url == "https://test.us.qlikcloud.com"
        assert config.qlik.api_key == "test-key"

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            Config.load("/nonexistent.yaml")

    def test_defaults_applied(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("qlik:\n  tenant_url: https://test.qlikcloud.com\n  api_key: k\n")

        config = Config.load(config_file)
        assert config.server.transport == "stdio"
        assert config.tools.max_hypercube_rows == 10000
        assert config.tools.get_sheet_details is True
        assert config.qlik.timeout_seconds == 30

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("QLIK_TENANT_URL", "https://env.qlikcloud.com")
        monkeypatch.setenv("QLIK_API_KEY", "env-key")

        config = Config.from_env()
        assert config.qlik.tenant_url == "https://env.qlikcloud.com"
        assert config.qlik.api_key == "env-key"


class TestConfigValidation:
    def test_valid_config(self):
        config = Config()
        config.qlik.tenant_url = "https://test.qlikcloud.com"
        config.qlik.api_key = "valid-key"

        errors = config.validate()
        assert errors == []

    def test_missing_tenant_url(self):
        config = Config()
        config.qlik.api_key = "valid-key"

        errors = config.validate()
        assert any("tenant_url" in e for e in errors)

    def test_no_auth(self):
        config = Config()
        config.qlik.tenant_url = "https://test.qlikcloud.com"

        errors = config.validate()
        assert any("Authentication" in e for e in errors)

    def test_invalid_transport(self):
        config = Config()
        config.qlik.tenant_url = "https://test.qlikcloud.com"
        config.qlik.api_key = "key"
        config.server.transport = "grpc"

        errors = config.validate()
        assert any("transport" in e for e in errors)

    def test_http_tenant_rejected(self):
        config = Config()
        config.qlik.tenant_url = "http://insecure.qlikcloud.com"
        config.qlik.api_key = "key"

        errors = config.validate()
        assert any("https://" in e for e in errors)


    def test_http_default_host_is_localhost(self):
        config = Config()
        assert config.server.http_host == "127.0.0.1"


class TestConfigProperties:
    def test_auth_mode_api_key(self):
        config = Config()
        config.qlik.api_key = "my-key"
        assert config.auth_mode == "api_key"

    def test_auth_mode_oauth(self):
        from qlik_mcp_server.config import OAuthConfig
        config = Config()
        config.qlik.oauth = OAuthConfig(
            client_id="id", client_secret="secret", token_url="https://t/oauth"
        )
        assert config.auth_mode == "oauth"

    def test_tenant_host(self):
        config = Config()
        config.qlik.tenant_url = "https://my-tenant.us.qlikcloud.com"
        assert config.tenant_host == "my-tenant.us.qlikcloud.com"


class TestTransportConfig:
    def test_streamable_http_is_valid_transport(self):
        config = Config()
        config.qlik.tenant_url = "https://test.qlikcloud.com"
        config.qlik.api_key = "key"
        config.server.transport = "streamable-http"
        assert config.validate() == []

    def test_legacy_sse_keys_still_load(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "qlik:\n  tenant_url: https://t.qlikcloud.com\n  api_key: k\n"
            "server:\n  transport: sse\n  sse_host: 0.0.0.0\n  sse_port: 9000\n"  # noqa: S104
        )
        config = Config.load(config_file)
        assert config.server.http_host == "0.0.0.0"  # noqa: S104
        assert config.server.http_port == 9000

    def test_from_env_reads_oauth(self, monkeypatch):
        monkeypatch.setenv("QLIK_TENANT_URL", "https://env.qlikcloud.com")
        monkeypatch.delenv("QLIK_API_KEY", raising=False)
        monkeypatch.setenv("QLIK_OAUTH_CLIENT_ID", "cid")
        monkeypatch.setenv("QLIK_OAUTH_CLIENT_SECRET", "sec")
        config = Config.from_env()
        assert config.auth_mode == "oauth"
        assert config.validate() == []


class TestToolEnablement:
    def test_all_tools_enabled_by_default(self):
        config = Config()
        assert config.tools.is_enabled("qlik_search")
        assert config.tools.is_enabled("qlik_list_bookmarks")

    def test_legacy_boolean_disables_tool(self):
        config = Config()
        config.tools.create_sheet = False
        assert not config.tools.is_enabled("qlik_create_sheet")
        assert config.tools.is_enabled("qlik_add_chart")

    def test_disabled_tools_list(self, tmp_path):
        config_file = tmp_path / "c.yaml"
        config_file.write_text(
            "qlik:\n  tenant_url: https://t.qlikcloud.com\n  api_key: k\n"
            "tools:\n  disabled_tools:\n    - qlik_add_filter\n    - qlik_search_field_values\n"
        )
        config = Config.load(config_file)
        assert not config.tools.is_enabled("qlik_add_filter")
        assert not config.tools.is_enabled("qlik_search_field_values")
        assert config.tools.is_enabled("qlik_search")

    def test_disabled_tools_from_env(self, monkeypatch):
        monkeypatch.setenv("QLIK_TENANT_URL", "https://env.qlikcloud.com")
        monkeypatch.setenv("QLIK_API_KEY", "k")
        monkeypatch.setenv("QLIK_MCP_DISABLED_TOOLS", "qlik_add_chart, qlik_add_filter")
        config = Config.from_env()
        assert not config.tools.is_enabled("qlik_add_chart")
        assert not config.tools.is_enabled("qlik_add_filter")
