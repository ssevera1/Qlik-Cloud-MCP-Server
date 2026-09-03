"""Tests for schema simplification, HTTP bearer auth, and tool group configuration."""


from starlette.testclient import TestClient

from qlik_mcp_server.config import Config
from qlik_mcp_server.server import build_http_app, create_server, simplify_schema

from .test_tool_contracts import FakeEngineClient, FakeRestClient


def _config(**server_overrides) -> Config:
    config = Config()
    config.qlik.tenant_url = "https://t.us.qlikcloud.com"
    config.qlik.api_key = "k"
    for k, v in server_overrides.items():
        setattr(config.server, k, v)
    return config


def _server(config=None):
    config = config or _config()
    return create_server(config, qlik_client=FakeRestClient(), engine_client=FakeEngineClient(config))


class TestSchemaSimplification:
    def test_inlines_refs_and_drops_nullable_unions(self):
        schema = {
            "$defs": {"Filter": {"type": "object", "properties": {"field": {"type": "string", "title": "Field"}}}},
            "properties": {
                "filters": {"anyOf": [{"items": {"$ref": "#/$defs/Filter"}, "type": "array"}, {"type": "null"}],
                            "default": None, "description": "f", "title": "Filters"},
                "limit": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}], "default": 20, "title": "Limit"},
            },
            "required": [],
            "title": "xArguments",
            "type": "object",
        }
        out = simplify_schema(schema)
        assert "$defs" not in out
        assert out["properties"]["filters"] == {
            "type": "array", "items": {"type": "object", "properties": {"field": {"type": "string"}}},
            "description": "f", "default": None,
        }
        assert out["properties"]["limit"] == {"type": "integer", "minimum": 1, "default": 20}
        assert "title" not in out

    async def test_every_registered_tool_schema_is_gemini_safe(self):
        server = _server()
        for tool in await server.list_tools():
            offending = _keys_used(tool.input_schema) & {"$ref", "$defs", "anyOf", "oneOf", "title", "allOf"}
            assert not offending, (tool.name, offending)

    async def test_simplified_schema_still_validates_calls(self):
        server = _server()
        result = await server.call_tool("qlik_create_data_object", {
            "app_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "dimensions": ["Region"],
            "measures": ["Sum(Sales)"], "filters": [{"field": "Region", "values": ["East"]}],
        })
        assert result.is_error is False
        assert "error" not in result.structured_content


def _keys_used(node) -> set:
    """Every dict key that appears anywhere in a JSON schema (values are ignored)."""
    keys: set = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            if key in ("required", "enum"):
                continue
            keys |= _keys_used(value)
    elif isinstance(node, list):
        for item in node:
            keys |= _keys_used(item)
    return keys


MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _http_client(token=None, stateless=True) -> TestClient:
    config = _config(http_bearer_token=token or "", http_stateless=stateless)
    # Host must match the loopback allow-list of the DNS-rebinding guard.
    return TestClient(build_http_app(_server(config), config), base_url="http://127.0.0.1:8080")


class TestHttpBearerAuth:
    def test_rejects_missing_token(self):
        with _http_client(token="secret") as client:
            r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=MCP_HEADERS)
        assert r.status_code == 401
        assert r.headers.get("www-authenticate", "").startswith("Bearer")

    def test_rejects_wrong_token(self):
        with _http_client(token="secret") as client:
            r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                            headers={**MCP_HEADERS, "Authorization": "Bearer nope"})
        assert r.status_code == 401

    def test_accepts_correct_token_and_lists_tools(self):
        with _http_client(token="secret") as client:
            auth = {**MCP_HEADERS, "Authorization": "Bearer secret"}
            init = client.post("/mcp", json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "1"}},
            }, headers=auth)
            assert init.status_code == 200, init.text
            r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=auth)
        assert r.status_code == 200, r.text
        assert "qlik_search" in r.text

    def test_health_endpoint_is_open(self):
        with _http_client(token="secret") as client:
            r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_no_token_configured_means_open(self):
        with _http_client(token=None) as client:
            r = client.get("/healthz")
        assert r.status_code == 200


class TestToolGroups:
    async def test_disabling_a_group_removes_its_tools(self):
        config = _config()
        config.tools.disabled_groups = ["selections", "bookmarks"]
        server = create_server(config, qlik_client=FakeRestClient(), engine_client=FakeEngineClient(config))
        names = {t.name for t in await server.list_tools()}
        assert "qlik_select_values" not in names
        assert "qlik_list_bookmarks" not in names
        assert "qlik_search" in names

    async def test_read_only_profile_hides_all_writes(self):
        config = _config()
        config.tools.allow_sheet_creation = False
        config.tools.allow_writes = False
        server = create_server(config, qlik_client=FakeRestClient(), engine_client=FakeEngineClient(config))
        names = {t.name for t in await server.list_tools()}
        assert "qlik_create_measure" not in names
        assert "qlik_delete_bookmark" not in names
        assert "qlik_create_sheet" not in names
        assert "qlik_select_values" in names  # session state only, not a persisted write
        for tool in await server.list_tools():
            assert tool.annotations.destructive_hint is False, tool.name

    async def test_delete_tools_are_marked_destructive(self):
        server = _server()
        tools = {t.name: t for t in await server.list_tools()}
        assert tools["qlik_delete_measure"].annotations.destructive_hint is True
        assert tools["qlik_create_measure"].annotations.destructive_hint is False
        assert tools["qlik_select_values"].annotations.read_only_hint is False
        assert tools["qlik_get_fields"].annotations.read_only_hint is True
