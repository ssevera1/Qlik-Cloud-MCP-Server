"""Behavioral tests for EngineSession against a fake Engine WebSocket."""

import pytest

from qlik_mcp_server.engine_client import EngineError, EngineSession

from .fakes import FakeWebSocket

DOC = 1
OBJ_HANDLE = 2
CREATE_HANDLE = 3
SESSION_HANDLE = 4


def _basic_responder(sheet_layouts=None) -> FakeWebSocket:
    sheet_layouts = sheet_layouts or {}
    state = {"last_object": None, "children": 0}

    def responder(msg: dict):
        method = msg["method"]
        if method == "GetObjects":
            return {"qList": [{"qInfo": {"qId": sid, "qType": "sheet"}} for sid in sheet_layouts]}
        if method == "GetObject":
            state["last_object"] = msg["params"][0]
            return {"qReturn": {"qType": "GenericObject", "qHandle": OBJ_HANDLE}}
        if method == "GetLayout" and msg["handle"] == OBJ_HANDLE:
            return sheet_layouts.get(state["last_object"], {})
        if method == "CreateObject":
            return {
                "qInfo": {"qId": "new-sheet", "qType": "sheet"},
                "qReturn": {"qType": "GenericObject", "qHandle": CREATE_HANDLE, "qGenericId": "new-sheet"},
            }
        if method == "CreateSessionObject":
            return {
                "qInfo": {"qId": "sess", "qType": "FieldList"},
                "qReturn": {"qType": "GenericObject", "qHandle": SESSION_HANDLE, "qGenericId": "sess"},
            }
        if method == "CreateChild":
            state["children"] += 1
            cid = f"child-{state['children']}"
            return {"qInfo": {"qId": cid}, "qReturn": {"qType": "GenericObject", "qHandle": 7, "qGenericId": cid}}
        if method == "GetProperties":
            return {"qProp": {"qInfo": {"qId": "new-sheet", "qType": "sheet"}, "cells": []}}
        if method in ("SetProperties", "DoSave"):
            return {}
        if method == "GetLayout" and msg["handle"] == SESSION_HANDLE:
            return {"qFieldList": {"qItems": [
                {"qName": "Region", "qCardinal": 4, "qTags": ["$ascii", "$text"],
                 "qSrcTables": ["Sales"], "qIsSystem": False, "qIsHidden": False},
                {"qName": "$Field", "qCardinal": 1, "qTags": ["$system"],
                 "qSrcTables": [], "qIsSystem": True, "qIsHidden": True},
            ]}}
        return {}

    return FakeWebSocket(responder)


class TestGetSheets:
    async def test_get_objects_uses_qtypes_option(self):
        ws = _basic_responder({"s1": {"qMeta": {"title": "Overview"}, "cells": []}})
        session = EngineSession(ws, doc_handle=DOC, app_id="app")

        sheets = await session.get_sheets()

        call = ws.calls("GetObjects")[0]
        assert call["handle"] == DOC
        assert call["params"][0]["qTypes"] == ["sheet"]
        assert "qType" not in call["params"][0]
        assert sheets[0]["id"] == "s1"
        assert sheets[0]["title"] == "Overview"

    async def test_get_objects_reads_qlist_result_shape(self):
        ws = _basic_responder({"s1": {"qMeta": {"title": "A"}}, "s2": {"qMeta": {"title": "B"}}})
        session = EngineSession(ws, doc_handle=DOC, app_id="app")

        sheets = await session.get_sheets()

        assert [s["id"] for s in sheets] == ["s1", "s2"]


class TestSheetLayout:
    async def test_cells_enriched_with_child_titles(self):
        layout = {
            "qMeta": {"title": "Sales"},
            "cells": [{"name": "obj-1", "type": "barchart", "col": 0, "row": 0, "colspan": 12, "rowspan": 6}],
            "qChildList": {"qItems": [
                {"qInfo": {"qId": "obj-1", "qType": "barchart"}, "qData": {"title": "Revenue by Region"}}
            ]},
        }
        ws = _basic_responder({"s1": layout})
        session = EngineSession(ws, doc_handle=DOC, app_id="app")

        described = session.describe_sheet(await session.get_sheet_layout("s1"))

        assert described["title"] == "Sales"
        obj = described["objects"][0]
        assert obj["id"] == "obj-1"
        assert obj["title"] == "Revenue by Region"
        assert obj["type"] == "barchart"


class TestCreateSheet:
    async def test_sheet_title_goes_in_qmetadef(self):
        ws = _basic_responder()
        session = EngineSession(ws, doc_handle=DOC, app_id="app")

        await session.create_sheet(title="[Agent] Test", description="desc")

        props = ws.calls("CreateObject")[0]["params"][0]
        assert props["qInfo"]["qType"] == "sheet"
        assert props["qMetaDef"]["title"] == "[Agent] Test"
        assert props["qMetaDef"]["description"] == "desc"
        assert props["columns"] == 24
        assert props["rows"] == 12

    async def test_children_are_placed_in_sheet_cells(self):
        ws = _basic_responder()
        session = EngineSession(ws, doc_handle=DOC, app_id="app")
        objects = [
            {"type": "barchart", "title": "A", "dimensions": ["Region"], "measures": ["Sum(X)"]},
            {"type": "kpi", "title": "B", "dimensions": [], "measures": ["Sum(X)"]},
        ]

        result = await session.create_sheet(title="T", objects=objects)

        assert result["object_count"] == 2
        set_props = ws.calls("SetProperties")[0]
        assert set_props["handle"] == CREATE_HANDLE
        cells = set_props["params"][0]["cells"]
        assert [c["type"] for c in cells] == ["barchart", "kpi"]
        assert [c["name"] for c in cells] == ["child-1", "child-2"]
        assert cells[0]["colspan"] > 0 and cells[0]["rowspan"] > 0
        assert cells[0]["bounds"]["width"] > 0

    def test_child_props_carry_visualization_and_initial_fetch(self):
        props = EngineSession._build_child_props(
            {"type": "linechart", "title": "Trend", "dimensions": ["Month"], "measures": ["Sum(Sales)"]}
        )
        assert props["visualization"] == "linechart"
        assert props["qInfo"]["qType"] == "linechart"
        assert props["title"] == "Trend"
        assert props["showTitles"] is True
        assert props["qHyperCubeDef"]["qInitialDataFetch"][0]["qHeight"] > 0

    async def test_sheet_is_saved_after_creation(self):
        ws = _basic_responder()
        session = EngineSession(ws, doc_handle=DOC, app_id="app")

        result = await session.create_sheet(title="T")

        saves = ws.calls("DoSave")
        assert len(saves) == 1
        assert saves[0]["handle"] == DOC
        assert result["saved"] is True
        assert ws.sent.index(saves[0]) > ws.sent.index(ws.calls("CreateObject")[0])


class TestGetFields:
    async def test_lists_fields_via_field_list_session_object(self):
        ws = _basic_responder()
        session = EngineSession(ws, doc_handle=DOC, app_id="app")

        fields = await session.get_fields()

        create = ws.calls("CreateSessionObject")[0]["params"][0]
        assert create["qFieldListDef"]["qShowSystem"] is False
        assert [f["name"] for f in fields] == ["Region"]
        assert fields[0]["cardinality"] == 4
        assert fields[0]["source_tables"] == ["Sales"]


class TestSendRobustness:
    async def test_skips_many_notifications_without_id(self):
        ws = _basic_responder()
        session = EngineSession(ws, doc_handle=DOC, app_id="app")
        real_send = ws.send

        async def send_and_flood(raw):
            await real_send(raw)
            for _ in range(150):
                ws.push_notification("OnChange", [1, 2])

        ws.send = send_and_flood

        result = await session._send("DoSave", DOC)

        assert result == {}

    async def test_engine_error_is_surfaced_with_code(self):
        ws = FakeWebSocket(lambda m: {"error": {"code": 1002, "message": "Access denied"}})
        session = EngineSession(ws, doc_handle=DOC, app_id="app")

        with pytest.raises(EngineError) as exc:
            await session._send("DoSave", DOC)
        assert exc.value.code == 1002
        assert "Access denied" in str(exc.value)


class TestSelections:
    async def test_select_values_reports_engine_return(self):
        def responder(msg):
            if msg["method"] == "GetField":
                return {"qReturn": {"qType": "Field", "qHandle": 5}}
            if msg["method"] == "SelectValues":
                return {"qReturn": False}
            return {}

        ws = FakeWebSocket(responder)
        session = EngineSession(ws, doc_handle=DOC, app_id="app")

        ok = await session.apply_selections("Region", ["Nowhere"])

        assert ok is False
        assert ws.calls("SelectValues")[0]["params"][0] == [{"qText": "Nowhere"}]
