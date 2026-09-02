"""Tests for MCP server construction and tool dispatch (in-process, no transport)."""

import json

from qlik_mcp_server.config import Config
from qlik_mcp_server.engine_client import EngineError
from qlik_mcp_server.server import TOOL_NAMES, create_server

APP_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def _config(**tool_flags) -> Config:
    config = Config()
    config.qlik.tenant_url = "https://tenant.us.qlikcloud.com"
    config.qlik.api_key = "key"
    for key, value in tool_flags.items():
        setattr(config.tools, key, value)
    return config


class FakeQlikClient:
    async def search_items(self, query, resource_type=None, space_id=None, limit=20):
        return [{"id": "1", "name": query}]


class FailingEngine:
    def __init__(self, config):
        self.config = config

    def open_app(self, app_id):
        raise EngineError("Failed to connect to Engine API")


def _server(**tool_flags):
    config = _config(**tool_flags)
    return create_server(config, qlik_client=FakeQlikClient(), engine_client=FailingEngine(config))


class TestToolRegistration:
    async def test_all_tools_registered_by_default(self):
        names = {t.name for t in await _server().list_tools()}
        assert names == set(TOOL_NAMES)
        assert "qlik_get_fields" in names

    async def test_disabled_tool_not_listed(self):
        names = {t.name for t in await _server(create_sheet=False).list_tools()}
        assert "qlik_create_sheet" not in names

    async def test_schemas_are_flat_with_descriptions(self):
        tool = {t.name: t for t in await _server().list_tools()}["qlik_get_hypercube_data"]
        props = tool.input_schema["properties"]
        assert set(props) >= {"app_id", "dimensions", "measures", "filters", "max_rows"}
        assert props["dimensions"]["description"]
        assert tool.input_schema["required"] == ["app_id", "dimensions", "measures"]


class TestToolInvocation:
    async def test_search_returns_structured_result(self):
        result = await _server().call_tool("qlik_search", {"query": "sales"})
        assert result.is_error is False
        assert result.structured_content["result_count"] == 1
        assert json.loads(result.content[0].text)["results"][0]["name"] == "sales"

    async def test_engine_failure_returns_error_payload_not_exception(self):
        result = await _server().call_tool("qlik_get_sheet_details", {"app_id": APP_ID})
        assert "error" in result.structured_content
        assert "Failed to connect" in result.structured_content["error"]

    async def test_invalid_resource_type_is_reported_as_error_payload(self):
        result = await _server().call_tool("qlik_search", {"query": "x", "resource_type": "bogus"})
        assert "error" in result.structured_content
        assert "resource_type" in result.structured_content["error"]
