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


    def test_sse_default_host_is_localhost(self):
        config = Config()
        assert config.server.sse_host == "127.0.0.1"


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
