"""Tests for session reuse, isolated filters, selections, bookmark and master-item writes."""

import asyncio

import pytest

from qlik_mcp_server.auth import AuthManager
from qlik_mcp_server.config import Config
from qlik_mcp_server.engine_client import EngineClient, EngineError, EngineSession, HypercubeResult

from .app_fixture import APP_ID, DOC, build_app_ws


def _session():
    ws = build_app_ws()
    return ws, EngineSession(ws, doc_handle=DOC, app_id=APP_ID)


class TestSelections:
    async def test_select_values_by_value(self):
        ws, session = _session()

        result = await session.select_values("Region", values=["East", "West"])

        assert ws.calls("GetField")[0]["params"] == ["Region"]
        assert ws.calls("SelectValues")[0]["params"][0] == [{"qText": "East"}, {"qText": "West"}]
        assert result["applied"] is True

    async def test_select_values_by_pattern_uses_field_select(self):
        ws, session = _session()

        await session.select_values("Region", match="Ea*")

        call = ws.calls("Select")[0]
        assert call["handle"] == 5
        assert call["params"][0] == "Ea*"

    async def test_select_requires_values_or_match(self):
        _, session = _session()
        with pytest.raises(EngineError, match="values or match"):
            await session.select_values("Region")

    async def test_clear_selections_all_and_per_field(self):
        ws, session = _session()

        await session.clear_selections()
        await session.clear_selections(["Region"])

        assert ws.calls("ClearAll")
        clear = ws.calls("Clear")[0]
        assert clear["handle"] == 5

    async def test_current_selections_from_selection_object(self):
        ws, session = _session()

        selections = await session.get_current_selections()

        create = ws.calls("CreateSessionObject")[0]["params"][0]
        assert "qSelectionObjectDef" in create
        assert selections == [{"field": "Region", "selected": ["East"], "selected_count": 1, "total": 3, "locked": False}]


class TestBookmarkWrites:
    async def test_create_bookmark_saves(self):
        ws, session = _session()

        result = await session.create_bookmark("East only", description="d")

        props = ws.calls("CreateBookmark")[0]["params"][0]
        assert props["qInfo"]["qType"] == "bookmark"
        assert props["qMetaDef"]["title"] == "East only"
        assert result["bookmark_id"] == "bm-new"
        assert ws.calls("DoSave")

    async def test_delete_bookmark_saves(self):
        ws, session = _session()

        assert await session.delete_bookmark("bm-1") is True
        assert ws.calls("DestroyBookmark")[0]["params"] == ["bm-1"]
        assert ws.calls("DoSave")


class TestMasterItemWrites:
    async def test_create_dimension(self):
        ws, session = _session()

        result = await session.create_dimension("Region", field_defs=["Region"], description="Sales region", tags=["geo"])

        props = ws.calls("CreateDimension")[0]["params"][0]
        assert props["qInfo"]["qType"] == "dimension"
        assert props["qDim"]["qFieldDefs"] == ["Region"]
        assert props["qDim"]["qGrouping"] == "N"
        assert props["qMetaDef"] == {"title": "Region", "description": "Sales region", "tags": ["geo"]}
        assert result["id"] == "dim-new"
        assert ws.calls("DoSave")

    async def test_create_measure(self):
        ws, session = _session()

        result = await session.create_measure("Margin", expression="Sum(Margin)/Sum(Sales)", label="Margin %")

        props = ws.calls("CreateMeasure")[0]["params"][0]
        assert props["qInfo"]["qType"] == "measure"
        assert props["qMeasure"]["qDef"] == "Sum(Margin)/Sum(Sales)"
        assert props["qMeasure"]["qLabel"] == "Margin %"
        assert result["id"] == "measure-new"

    async def test_update_measure_merges_properties(self):
        ws, session = _session()

        await session.update_measure("measure-lib-1", expression="Sum(Margin)")

        assert ws.calls("GetMeasure")[0]["params"] == ["measure-lib-1"]
        set_props = ws.calls("SetProperties")[0]["params"][0]
        assert set_props["qMeasure"]["qDef"] == "Sum(Margin)"
        assert set_props["qMetaDef"]["title"] == "Margin"  # untouched field preserved
        assert ws.calls("DoSave")

    async def test_delete_dimension_and_measure(self):
        ws, session = _session()

        assert await session.delete_dimension("dim-1") is True
        assert await session.delete_measure("measure-lib-1") is True
        assert ws.calls("DestroyDimension")[0]["params"] == ["dim-1"]
        assert ws.calls("DestroyMeasure")[0]["params"] == ["measure-lib-1"]


class TestScript:
    async def test_get_script(self):
        ws, session = _session()
        assert await session.get_script() == "LOAD * FROM Sales;"
        assert ws.calls("GetScript")[0]["handle"] == DOC


class TestIsolatedFilters:
    async def test_filters_use_a_temporary_alternate_state(self):
        ws, session = _session()

        result = await session.create_hypercube(
            ["Region"], ["Sum(Sales)"], filters=[{"field": "Region", "values": ["East"]}],
        )

        methods = [m["method"] for m in ws.sent]
        assert "AddAlternateState" in methods
        state = ws.calls("AddAlternateState")[0]["params"][0]
        assert ws.calls("GetField")[0]["params"] == ["Region", state]
        create = ws.calls("CreateSessionObject")[0]["params"][0]
        assert create["qHyperCubeDef"]["qStateName"] == state
        assert methods.index("RemoveAlternateState") > methods.index("GetLayout")
        assert result.rows
        assert "ClearAll" not in methods

    async def test_temporary_objects_are_destroyed_on_cleanup(self):
        ws, session = _session()

        await session.get_fields()
        assert not ws.calls("DestroySessionObject")

        await session.cleanup_temp()

        assert ws.calls("DestroySessionObject")[0]["params"] == ["session-100"]
        assert session._temp_objects == []


class TestOutputFormats:
    def test_markdown_and_csv(self):
        result = HypercubeResult(headers=["Region", "Sales"], rows=[["East", 100], ["West", 50]], total_rows=2)
        md = result.as_payload(fmt="markdown")
        assert "| Region | Sales |" in md["table"]
        assert "data" not in md
        csv = result.as_payload(fmt="csv")
        assert csv["csv"].splitlines()[0] == "Region,Sales"
        js = result.as_payload()
        assert js["columns"] == ["Region", "Sales"]
        assert js["rows"] == [["East", 100], ["West", 50]]
        assert "table" not in js


class TestSessionPool:
    async def test_pool_reuses_connection_and_serializes_per_app(self, monkeypatch):
        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "k"
        config.qlik.reuse_sessions = True
        config.qlik.session_idle_seconds = 60
        connections = []

        async def fake_connect(url, **kwargs):
            ws = build_app_ws()
            connections.append(ws)
            return ws

        monkeypatch.setattr("qlik_mcp_server.engine_client.ws_connect", fake_connect)
        client = EngineClient(config, AuthManager(config))

        async with client.open_app(APP_ID) as s1:
            await s1.list_sheets()
        async with client.open_app(APP_ID) as s2:
            await s2.list_sheets()

        assert len(connections) == 1
        assert not connections[0].closed
        assert len(connections[0].calls("OpenDoc")) == 1

        await client.close()
        assert connections[0].closed

    async def test_pool_expires_idle_sessions(self, monkeypatch):
        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "k"
        config.qlik.session_idle_seconds = 0
        connections = []

        async def fake_connect(url, **kwargs):
            ws = build_app_ws()
            connections.append(ws)
            return ws

        monkeypatch.setattr("qlik_mcp_server.engine_client.ws_connect", fake_connect)
        client = EngineClient(config, AuthManager(config))

        async with client.open_app(APP_ID):
            pass
        await asyncio.sleep(0.01)
        async with client.open_app(APP_ID):
            pass

        assert len(connections) == 2
        assert connections[0].closed
        await client.close()

    async def test_pool_disabled_opens_per_call(self, monkeypatch):
        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "k"
        config.qlik.reuse_sessions = False
        connections = []

        async def fake_connect(url, **kwargs):
            ws = build_app_ws()
            connections.append(ws)
            return ws

        monkeypatch.setattr("qlik_mcp_server.engine_client.ws_connect", fake_connect)
        client = EngineClient(config, AuthManager(config))

        async with client.open_app(APP_ID):
            pass
        async with client.open_app(APP_ID):
            pass

        assert len(connections) == 2
        assert all(c.closed for c in connections)

    async def test_broken_socket_is_evicted(self, monkeypatch):
        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "k"
        connections = []

        async def fake_connect(url, **kwargs):
            ws = build_app_ws()
            connections.append(ws)
            return ws

        monkeypatch.setattr("qlik_mcp_server.engine_client.ws_connect", fake_connect)
        client = EngineClient(config, AuthManager(config))

        async with client.open_app(APP_ID) as s:
            await s.list_sheets()

        async def boom(raw):
            raise ConnectionError("socket gone")

        connections[0].send = boom
        with pytest.raises(EngineError):
            async with client.open_app(APP_ID) as s:
                await s.list_sheets()

        async with client.open_app(APP_ID) as s:
            await s.list_sheets()
        assert len(connections) == 2
        await client.close()
