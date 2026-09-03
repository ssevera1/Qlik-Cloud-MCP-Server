"""Tests for the tools.profile setting and its environment override."""

from qlik_mcp_server.config import ANALYTICS_GROUPS, Config
from qlik_mcp_server.server import create_server
from qlik_mcp_server.tools.registry import TOOL_SPECS, enabled_specs

from .test_tool_contracts import FakeEngineClient, FakeRestClient


def _config(profile: str) -> Config:
    config = Config()
    config.qlik.tenant_url = "https://t.us.qlikcloud.com"
    config.qlik.api_key = "k"
    config.tools.profile = profile
    return config


class TestProfiles:
    def test_full_profile_exposes_everything(self):
        assert [s.name for s in enabled_specs(_config("full"))] == [s.name for s in TOOL_SPECS]

    def test_analytics_profile_keeps_only_app_groups(self):
        specs = enabled_specs(_config("analytics"))
        assert specs, "analytics profile must not be empty"
        assert {s.group for s in specs} <= ANALYTICS_GROUPS
        names = {s.name for s in specs}
        assert {"qlik_search", "qlik_create_data_object", "qlik_ask_question", "qlik_get_lineage"} <= names
        assert not any(n.startswith("qlik_list_automation") for n in names)
        assert "qlik_get_dataset" not in names

    def test_readonly_profile_hides_writes_but_keeps_session_tools(self):
        specs = enabled_specs(_config("readonly"))
        assert not any(s.writes for s in specs)
        names = {s.name for s in specs}
        assert "qlik_select_values" in names
        assert "qlik_list_automations" in names
        assert "qlik_create_sheet" not in names

    def test_invalid_profile_is_a_validation_error(self):
        config = _config("everything")
        assert any("tools.profile" in e for e in config.validate())

    def test_profile_from_env(self, monkeypatch):
        monkeypatch.setenv("QLIK_TENANT_URL", "https://env.qlikcloud.com")
        monkeypatch.setenv("QLIK_API_KEY", "k")
        monkeypatch.setenv("QLIK_MCP_PROFILE", "Analytics")
        config = Config.from_env()
        assert config.tools.profile == "analytics"
        assert config.validate() == []

    def test_profile_from_yaml(self, tmp_path):
        config_file = tmp_path / "c.yaml"
        config_file.write_text(
            "qlik:\n  tenant_url: https://t.qlikcloud.com\n  api_key: k\ntools:\n  profile: readonly\n"
        )
        config = Config.load(config_file)
        assert config.tools.profile == "readonly"
        assert config.tools.writes_allowed is False

    async def test_analytics_server_is_much_smaller(self):
        full = create_server(_config("full"), qlik_client=FakeRestClient(), engine_client=FakeEngineClient(_config("full")))
        small = create_server(_config("analytics"), qlik_client=FakeRestClient(),
                              engine_client=FakeEngineClient(_config("analytics")))
        assert len(await small.list_tools()) < len(await full.list_tools()) / 2

    def test_unresolved_token_placeholder_does_not_become_the_token(self, tmp_path):
        config_file = tmp_path / "c.yaml"
        config_file.write_text(
            "qlik:\n  tenant_url: https://t.qlikcloud.com\n  api_key: k\n"
            "server:\n  http_bearer_token: ${UNSET_TOKEN_VAR_XYZ}\n"
        )
        config = Config.load(config_file)
        assert config.server.http_bearer_token == ""
