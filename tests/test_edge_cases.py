"""Edge-case tests for tool handlers, engine parsing, config, and REST retries."""

from contextlib import asynccontextmanager

import httpx
import pytest

from qlik_mcp_server.auth import AuthManager
from qlik_mcp_server.config import Config, _resolve_dict
from qlik_mcp_server.engine_client import EngineSession, _rows_from_pages
from qlik_mcp_server.qlik_cloud_client import QlikCloudClient
from qlik_mcp_server.tools.get_hypercube_data import handle_get_hypercube_data
from qlik_mcp_server.tools.create_sheet import handle_create_sheet

from .fakes import FakeWebSocket

APP_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
DOC = 1


class FakeEngineClient:
    """EngineClient stand-in that yields a real EngineSession over a FakeWebSocket."""

    def __init__(self, ws: FakeWebSocket):
        self.ws = ws
        self.config = Config()
        self.config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        self.opened = 0

    @asynccontextmanager
    async def open_app(self, app_id: str):
        self.opened += 1
        yield EngineSession(self.ws, doc_handle=DOC, app_id=app_id)


def _hypercube_ws(total_rows: int = 3, select_ok: bool = True) -> FakeWebSocket:
    def responder(msg):
        m = msg["method"]
        if m == "GetField":
            return {"qReturn": {"qHandle": 5}}
        if m == "SelectValues":
            return {"qReturn": select_ok}
        if m == "CreateSessionObject":
            return {"qReturn": {"qHandle": 4}}
        if m == "GetLayout":
            return {"qLayout": {"qHyperCube": {
                "qDimensionInfo": [{"qFallbackTitle": "Region"}],
                "qMeasureInfo": [{"qFallbackTitle": "Sum(X)"}],
                "qSize": {"qcx": 2, "qcy": total_rows},
                "qDataPages": [{"qMatrix": [
                    [{"qText": "East"}, {"qText": "1", "qNum": 1}],
                ]}],
            }}}
        if m == "GetHyperCubeData":
            page = msg["params"][1][0]
            n = page["qHeight"]
            return {"qDataPages": [{"qMatrix": [[{"qText": f"R{page['qTop'] + i}"}, {"qNum": i}] for i in range(n)]}]}
        return {}
    return FakeWebSocket(responder)


class TestHypercubeHandler:
    async def test_max_rows_is_clamped_to_server_limit(self):
        ws = _hypercube_ws(total_rows=500)
        result = await handle_get_hypercube_data(
            FakeEngineClient(ws),
            {"app_id": APP_ID, "dimensions": ["Region"], "measures": ["Sum(X)"], "max_rows": 999999},
            max_rows_limit=5,
        )
        assert result["row_count"] == 5
        assert result["truncated"] is True
        create = ws.calls("CreateSessionObject")[0]["params"][0]
        assert create["qHyperCubeDef"]["qInitialDataFetch"][0]["qHeight"] <= 5

    async def test_column_limit_rejected_before_connecting(self):
        engine = FakeEngineClient(_hypercube_ws())
        result = await handle_get_hypercube_data(
            engine,
            {"app_id": APP_ID, "dimensions": ["A", "B"], "measures": ["Sum(X)"]},
            max_columns_limit=2,
        )
        assert "error" in result
        assert engine.opened == 0

    async def test_unmatched_filter_is_reported(self):
        ws = _hypercube_ws(select_ok=False)
        result = await handle_get_hypercube_data(
            FakeEngineClient(ws),
            {"app_id": APP_ID, "dimensions": ["Region"], "measures": ["Sum(X)"],
             "filters": [{"field": "Region", "values": ["Nowhere"]}]},
        )
        assert result["filters_not_matched"] == [{"field": "Region", "values": ["Nowhere"]}]
        assert "warning" in result

    async def test_filters_applied_before_hypercube(self):
        ws = _hypercube_ws()
        await handle_get_hypercube_data(
            FakeEngineClient(ws),
            {"app_id": APP_ID, "dimensions": ["Region"], "measures": ["Sum(X)"],
             "filters": [{"field": "Year", "values": ["2025"]}]},
        )
        methods = [m["method"] for m in ws.sent]
        assert methods.index("SelectValues") < methods.index("CreateSessionObject")

    async def test_invalid_expression_surfaces_engine_error(self):
        def responder(msg):
            if msg["method"] == "CreateSessionObject":
                return {"qReturn": {"qHandle": 4}}
            if msg["method"] == "GetLayout":
                return {"qLayout": {"qHyperCube": {
                    "qDimensionInfo": [{"qFallbackTitle": "Bad", "qError": {"qErrorCode": 7005}}],
                    "qMeasureInfo": [], "qSize": {"qcy": 0}, "qDataPages": [],
                }}}
            return {}
        result = await handle_get_hypercube_data(
            FakeEngineClient(FakeWebSocket(responder)),
            {"app_id": APP_ID, "dimensions": ["NoSuchField"], "measures": ["Sum(X)"]},
        )
        assert "error" in result
        assert "7005" in result["error"]


class TestEngineParsing:
    def test_rows_from_pages_handles_null_cells(self):
        rows = _rows_from_pages([{"qMatrix": [[{"qText": None, "qNum": None}, {"qNum": 2.5}]]}])
        assert rows == [["", 2.5]]

    async def test_send_ignores_non_object_json_messages(self):
        ws = FakeWebSocket(lambda m: {"ok": True})
        real_send = ws.send

        async def send_with_noise(raw):
            await real_send(raw)
            ws._queue.insert(0, "[1, 2, 3]")
            ws._queue.insert(0, "42")

        ws.send = send_with_noise
        session = EngineSession(ws, doc_handle=DOC, app_id="app")
        assert await session._send("Ping", DOC) == {"ok": True}

    def test_layout_cells_stay_within_grid_for_all_sizes(self):
        for n in range(1, 25):
            cells = EngineSession._layout_cells([{"id": f"o{i}", "type": "kpi"} for i in range(n)])
            assert len(cells) == n
            for c in cells:
                assert 0 <= c["col"] and c["col"] + c["colspan"] <= 24
                assert 0 <= c["row"] and c["row"] + c["rowspan"] <= 12
            positions = {(c["col"], c["row"]) for c in cells}
            assert len(positions) == n

    def test_describe_sheet_tolerates_malformed_child_list(self):
        layout = {
            "qMeta": None,
            "cells": [{"name": "a", "type": "kpi"}],
            "qChildList": {"qItems": [{"qInfo": {"qId": "a"}, "qData": {"title": {"not": "a string"}}}, {}]},
        }
        described = EngineSession.describe_sheet(layout)
        assert described["objects"][0]["title"] == ""
        assert described["title"] == ""


class TestCreateSheetHandler:
    async def test_disabled_creation_short_circuits(self):
        engine = FakeEngineClient(FakeWebSocket(lambda m: {}))
        result = await handle_create_sheet(engine, {"app_id": APP_ID, "title": "x"}, allow_creation=False)
        assert "error" in result
        assert engine.opened == 0

    async def test_partial_child_failure_is_reported_not_fatal(self):
        state = {"n": 0}

        def responder(msg):
            m = msg["method"]
            if m == "CreateObject":
                return {"qReturn": {"qHandle": 3, "qGenericId": "sheet1"}}
            if m == "CreateChild":
                state["n"] += 1
                if state["n"] == 1:
                    return {"error": {"code": 9, "message": "bad expression"}}
                return {"qReturn": {"qHandle": 7, "qGenericId": "child-ok"}}
            if m == "GetProperties":
                return {"qProp": {"qInfo": {"qId": "sheet1"}}}
            return {}

        result = await handle_create_sheet(
            FakeEngineClient(FakeWebSocket(responder)),
            {"app_id": APP_ID, "title": "T", "objects": [
                {"type": "kpi", "title": "Broken", "measures": ["Sum("]},
                {"type": "kpi", "title": "Fine", "measures": ["Sum(X)"]},
            ]},
        )
        assert result["object_count"] == 1
        assert len(result["failed_objects"]) == 1
        assert "Broken" in result["failed_objects"][0]
        assert result["saved"] is True


class TestConfigEdgeCases:
    def test_non_integer_port_reported_not_raised(self):
        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "k"
        config.server.http_port = "abc"  # type: ignore[assignment]
        errors = config.validate()
        assert any("http_port" in e for e in errors)

    def test_env_vars_resolved_inside_lists_and_nested_dicts(self, monkeypatch):
        monkeypatch.setenv("EDGE_A", "1")
        resolved = _resolve_dict({"x": {"y": ["${EDGE_A}", {"z": "${EDGE_A}"}, 5]}})
        assert resolved == {"x": {"y": ["1", {"z": "1"}, 5]}}

    def test_empty_sections_do_not_crash(self, tmp_path):
        config_file = tmp_path / "c.yaml"
        config_file.write_text("qlik:\n  tenant_url: https://t.qlikcloud.com\n  api_key: k\nserver:\ntools:\n")
        config = Config.load(config_file)
        assert config.server.transport == "stdio"


class TestRestRetries:
    async def test_retry_after_is_capped(self, monkeypatch):
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr("qlik_mcp_server.qlik_cloud_client.asyncio.sleep", fake_sleep)
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "100000"})
            return httpx.Response(200, json={"data": []})

        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "k"
        client = QlikCloudClient(config, AuthManager(config), transport=httpx.MockTransport(handler))
        await client.search_items("x")
        assert sleeps and sleeps[0] <= 60

    async def test_persistent_429_gives_clear_error(self, monkeypatch):
        async def fake_sleep(seconds):
            pass

        monkeypatch.setattr("qlik_mcp_server.qlik_cloud_client.asyncio.sleep", fake_sleep)
        config = Config()
        config.qlik.tenant_url = "https://t.us.qlikcloud.com"
        config.qlik.api_key = "k"
        config.qlik.max_retries = 2
        client = QlikCloudClient(config, AuthManager(config),
                                 transport=httpx.MockTransport(lambda r: httpx.Response(429)))
        from qlik_mcp_server.qlik_cloud_client import QlikCloudError
        with pytest.raises(QlikCloudError, match="rate limit"):
            await client.search_items("x")
