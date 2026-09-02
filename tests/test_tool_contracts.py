"""Full-stack contract tests: every tool invoked through the MCP server against a fake app.

These run the real SDK validation and result conversion, the real tool handlers,
and the real EngineSession wire encoding. Only the network is faked.
"""

from contextlib import asynccontextmanager

import pytest

from qlik_mcp_server.config import Config
from qlik_mcp_server.engine_client import EngineSession
from qlik_mcp_server.server import TOOL_NAMES, create_server

from .app_fixture import APP_ID, CHART_ID, DOC, LISTBOX_ID, SHEET_ID, build_app_ws

EXPECTED_TOOLS = (
    "qlik_search",
    "qlik_describe_app",
    "qlik_get_fields",
    "qlik_get_field_values",
    "qlik_search_field_values",
    "qlik_list_sheets",
    "qlik_get_sheet_details",
    "qlik_get_chart_info",
    "qlik_get_chart_data",
    "qlik_list_dimensions",
    "qlik_list_measures",
    "qlik_list_bookmarks",
    "qlik_get_hypercube_data",
    "qlik_create_sheet",
    "qlik_add_chart",
    "qlik_add_filter",
)

WRITE_TOOLS = {"qlik_create_sheet", "qlik_add_chart", "qlik_add_filter"}


class FakeEngineClient:
    def __init__(self, config: Config):
        self.config = config
        self.ws = build_app_ws()
        self.opened = 0

    @asynccontextmanager
    async def open_app(self, app_id: str):
        assert app_id == APP_ID
        self.opened += 1
        yield EngineSession(self.ws, doc_handle=DOC, app_id=app_id)


class FakeRestClient:
    async def search_items(self, query, resource_type=None, space_id=None, limit=20):
        return [{"id": "i1", "resource_id": APP_ID, "name": "Sales Analysis", "resource_type": "app",
                 "url": f"https://t.us.qlikcloud.com/sense/app/{APP_ID}"}]

    async def get_app(self, app_id):
        return {"id": app_id, "name": "Sales Analysis", "description": "Regional sales",
                "ownerId": "owner-1", "spaceId": "space-1", "lastReloadTime": "2026-08-30T10:00:00Z",
                "published": True, "hasSectionAccess": False, "usage": "ANALYTICS"}

    async def get_app_data_metadata(self, app_id):
        return {"fields": [{"name": "Region"}, {"name": "Sales"}],
                "tables": [{"name": "Sales", "no_of_rows": 40, "no_of_fields": 2}]}


def _config(**tool_overrides) -> Config:
    config = Config()
    config.qlik.tenant_url = "https://t.us.qlikcloud.com"
    config.qlik.api_key = "k"
    for k, v in tool_overrides.items():
        setattr(config.tools, k, v)
    return config


@pytest.fixture
def stack():
    config = _config()
    engine = FakeEngineClient(config)
    server = create_server(config, qlik_client=FakeRestClient(), engine_client=engine)
    return server, engine


async def _call(server, name, args):
    result = await server.call_tool(name, args)
    assert result.is_error is False, result.content
    payload = result.structured_content
    assert "error" not in payload, payload
    return payload


class TestRegistry:
    async def test_all_expected_tools_exposed_in_order(self, stack):
        server, _ = stack
        assert TOOL_NAMES == EXPECTED_TOOLS
        assert [t.name for t in await server.list_tools()] == list(EXPECTED_TOOLS)

    async def test_read_only_annotations(self, stack):
        server, _ = stack
        for tool in await server.list_tools():
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is (tool.name not in WRITE_TOOLS), tool.name
            assert tool.annotations.destructive_hint is False, tool.name

    async def test_every_tool_has_description_and_flat_schema(self, stack):
        server, _ = stack
        for tool in await server.list_tools():
            assert tool.description and len(tool.description) > 40, tool.name
            assert tool.input_schema["type"] == "object"
            for prop in tool.input_schema["properties"].values():
                assert "description" in prop, tool.name

    async def test_write_tools_hidden_when_sheet_creation_disallowed(self):
        config = _config(allow_sheet_creation=False)
        server = create_server(config, qlik_client=FakeRestClient(), engine_client=FakeEngineClient(config))
        names = {t.name for t in await server.list_tools()}
        assert not (names & WRITE_TOOLS)
        assert "qlik_search" in names

    async def test_disabled_tools_list_removes_tools(self):
        config = _config(disabled_tools=["qlik_search_field_values", "qlik_list_bookmarks"])
        server = create_server(config, qlik_client=FakeRestClient(), engine_client=FakeEngineClient(config))
        names = {t.name for t in await server.list_tools()}
        assert "qlik_search_field_values" not in names
        assert "qlik_list_bookmarks" not in names
        assert len(names) == len(EXPECTED_TOOLS) - 2


class TestDiscoveryTools:
    async def test_search(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_search", {"query": "sales", "resource_type": "app"})
        assert payload["results"][0]["resource_id"] == APP_ID

    async def test_describe_app_merges_rest_and_engine(self, stack):
        server, engine = stack
        payload = await _call(server, "qlik_describe_app", {"app_id": APP_ID})
        assert payload["name"] == "Sales Analysis"
        assert payload["last_reload_time"] == "2026-08-30T10:00:00Z"
        assert payload["has_section_access"] is False
        assert payload["url"].endswith(f"/sense/app/{APP_ID}")
        assert payload["tables"] == [{"name": "Sales", "rows": 40, "fields": 2}]
        assert payload["sheet_count"] == 1
        assert payload["master_dimension_count"] == 1
        assert payload["master_measure_count"] == 1
        assert payload["bookmark_count"] == 1
        assert engine.opened == 1

    async def test_get_fields(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_get_fields", {"app_id": APP_ID})
        assert [f["name"] for f in payload["fields"]] == ["Region", "Sales"]

    async def test_get_field_values(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_get_field_values", {"app_id": APP_ID, "field": "Region", "max_values": 2})
        assert payload["field"] == "Region"
        assert payload["values"][0]["value"] == "East"
        assert payload["total_values"] == 3

    async def test_search_field_values(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_search_field_values", {"app_id": APP_ID, "terms": ["east"]})
        assert payload["matches"][0] == {"field": "Region", "values": ["East", "West"]}


class TestSheetAndChartTools:
    async def test_list_sheets(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_list_sheets", {"app_id": APP_ID})
        assert payload["sheets"] == [{"id": SHEET_ID, "title": "Overview", "description": "Main sheet",
                                     "published": True, "rank": 0}]

    async def test_get_sheet_details_specific_sheet(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_get_sheet_details", {"app_id": APP_ID, "sheet_id": SHEET_ID})
        assert payload["title"] == "Overview"
        assert payload["objects"][0] == {
            "id": CHART_ID, "name": CHART_ID, "type": "barchart", "title": "Sales by Region",
            "bounds": {"x": 0, "y": 0, "width": 12, "height": 6},
        }

    async def test_get_chart_info(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_get_chart_info", {"app_id": APP_ID, "object_id": CHART_ID})
        assert payload["type"] == "barchart"
        assert payload["measures"][1]["library_id"] == "measure-lib-1"

    async def test_get_chart_data(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_get_chart_data", {"app_id": APP_ID, "object_id": CHART_ID})
        assert payload["headers"] == ["Region", "Sales", "Margin"]
        assert payload["row_count"] == 2
        assert "table" in payload

    async def test_get_chart_data_listbox(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_get_chart_data", {"app_id": APP_ID, "object_id": LISTBOX_ID})
        assert payload["data"] == [["East"], ["West"]]


class TestMasterItemTools:
    async def test_list_dimensions(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_list_dimensions", {"app_id": APP_ID})
        assert payload["dimensions"][0]["field_defs"] == ["Region"]

    async def test_list_measures(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_list_measures", {"app_id": APP_ID})
        assert payload["measures"][0]["expression"] == "Sum(Margin)/Sum(Sales)"

    async def test_list_bookmarks(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_list_bookmarks", {"app_id": APP_ID})
        assert payload["bookmarks"][0]["id"] == "bm-1"


class TestDataTools:
    async def test_hypercube_with_bookmark(self, stack):
        server, engine = stack
        payload = await _call(server, "qlik_get_hypercube_data", {
            "app_id": APP_ID, "dimensions": ["Region"], "measures": ["Sum(Sales)"], "bookmark_id": "bm-1",
        })
        assert payload["bookmark_applied"] is True
        methods = [m["method"] for m in engine.ws.sent]
        assert methods.index("ApplyBookmark") < methods.index("CreateSessionObject")

    async def test_hypercube_unknown_bookmark_is_an_error(self, stack):
        server, _ = stack
        result = await server.call_tool("qlik_get_hypercube_data", {
            "app_id": APP_ID, "dimensions": ["Region"], "measures": ["Sum(Sales)"], "bookmark_id": "nope",
        })
        assert "error" in result.structured_content
        assert "bookmark" in result.structured_content["error"].lower()


class TestWriteTools:
    async def test_create_sheet(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_create_sheet", {
            "app_id": APP_ID, "title": "Agent view",
            "objects": [{"type": "kpi", "title": "Total", "measures": ["Sum(Sales)"]}],
        })
        assert payload["saved"] is True
        assert payload["title"].startswith("[Agent] ")

    async def test_add_chart(self, stack):
        server, engine = stack
        payload = await _call(server, "qlik_add_chart", {
            "app_id": APP_ID, "sheet_id": SHEET_ID, "type": "linechart",
            "title": "Trend", "dimensions": ["Month"], "measures": ["Sum(Sales)"],
        })
        assert payload["object_id"] == "child-1"
        assert payload["saved"] is True
        assert payload["url"].endswith(f"/sheet/{SHEET_ID}/state/analysis")
        assert engine.ws.calls("DoSave")

    async def test_add_chart_rejects_unknown_type(self, stack):
        server, engine = stack
        result = await server.call_tool("qlik_add_chart", {
            "app_id": APP_ID, "sheet_id": SHEET_ID, "type": "hologram", "measures": ["Sum(Sales)"],
        })
        assert "error" in result.structured_content
        assert engine.opened == 0

    async def test_add_filter(self, stack):
        server, _ = stack
        payload = await _call(server, "qlik_add_filter", {
            "app_id": APP_ID, "sheet_id": SHEET_ID, "fields": ["Region", "Year"],
        })
        assert payload["filter_pane_id"] == "child-1"
        assert payload["fields"] == ["Region", "Year"]
        assert payload["saved"] is True
