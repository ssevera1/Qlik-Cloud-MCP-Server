"""Tests for the engine methods behind the extended (Qlik-parity) tool set."""

import pytest

from qlik_mcp_server.engine_client import EngineError, EngineSession

from .app_fixture import CHART_ID, DOC, LISTBOX_ID, SHEET_ID, build_app_ws


def _session():
    ws = build_app_ws()
    return ws, EngineSession(ws, doc_handle=DOC, app_id="app")


class TestListSheets:
    async def test_lists_sheets_from_get_objects_meta_without_layout_calls(self):
        ws, session = _session()

        sheets = await session.list_sheets()

        assert sheets == [{"id": SHEET_ID, "title": "Overview", "description": "Main sheet",
                           "published": True, "rank": 0}]
        assert ws.calls("GetLayout") == []


class TestAppLayout:
    async def test_get_app_layout_returns_title_and_reload_time(self):
        ws, session = _session()

        layout = await session.get_app_layout()

        assert layout["qTitle"] == "Sales Analysis"
        assert ws.calls("GetAppLayout")[0]["handle"] == DOC


class TestFieldValues:
    async def test_field_values_use_list_object_with_frequency(self):
        ws, session = _session()

        result = await session.get_field_values("Region", max_values=50)

        create = ws.calls("CreateSessionObject")[0]["params"][0]
        lo = create["qListObjectDef"]
        assert lo["qDef"]["qFieldDefs"] == ["Region"]
        assert lo["qFrequencyMode"] == "V"
        assert lo["qInitialDataFetch"][0]["qHeight"] == 50
        assert result["total_values"] == 3
        assert result["values"][0] == {"value": "East", "state": "selected", "frequency": 10}
        assert result["values"][1]["state"] == "optional"
        assert result["values"][2]["state"] == "excluded"

    async def test_field_values_search_filters_with_search_list_object_data(self):
        ws, session = _session()

        await session.get_field_values("Region", max_values=10, match="ea")

        assert ws.calls("SearchListObjectFor")[0]["params"] == ["/qListObjectDef", "ea"]


class TestSearchFieldValues:
    async def test_search_results_request_and_grouping(self):
        ws, session = _session()

        result = await session.search_field_values(["east"], fields=["Region", "City"], max_matches=5)

        call = ws.calls("SearchResults")[0]
        assert call["handle"] == DOC
        options, terms, page = call["params"]
        assert options["qSearchFields"] == ["Region", "City"]
        assert options["qContext"] == "Cleared"
        assert terms == ["east"]
        assert page["qMaxNbrFieldMatches"] == 5
        assert result["matches"] == [
            {"field": "Region", "values": ["East", "West"]},
            {"field": "City", "values": ["Easton"]},
        ]


class TestMasterItems:
    async def test_master_dimensions_and_measures(self):
        ws, session = _session()

        items = await session.get_master_items()

        create = ws.calls("CreateSessionObject")[0]["params"][0]
        assert create["qDimensionListDef"]["qType"] == "dimension"
        assert create["qMeasureListDef"]["qType"] == "measure"
        assert create["qDimensionListDef"]["qData"]["qFieldDefs"] == "/qDim/qFieldDefs"
        assert create["qMeasureListDef"]["qData"]["qDef"] == "/qMeasure/qDef"
        assert items["dimensions"] == [{
            "id": "dim-1", "title": "Region", "description": "Sales region",
            "tags": ["geo"], "field_defs": ["Region"], "grouping": "N",
        }]
        assert items["measures"] == [{
            "id": "measure-lib-1", "title": "Margin", "description": "Gross margin",
            "tags": [], "expression": "Sum(Margin)/Sum(Sales)",
        }]

    async def test_bookmarks(self):
        ws, session = _session()

        bookmarks = await session.get_bookmarks()

        create = ws.calls("CreateSessionObject")[0]["params"][0]
        assert create["qBookmarkListDef"]["qType"] == "bookmark"
        assert bookmarks == [{
            "id": "bm-1", "title": "East only", "description": "",
            "sheet_id": SHEET_ID, "selection_fields": "Region", "created": "2026-01-01",
        }]

    async def test_apply_bookmark_reports_success_flag(self):
        ws, session = _session()

        assert await session.apply_bookmark("bm-1") is True
        assert await session.apply_bookmark("nope") is False
        assert ws.calls("ApplyBookmark")[0] == {
            "jsonrpc": "2.0", "id": 1, "method": "ApplyBookmark", "handle": DOC, "params": ["bm-1"],
        }


class TestChartAccess:
    async def test_chart_info_resolves_dimensions_measures_and_library_ids(self):
        ws, session = _session()

        info = await session.get_object_info(CHART_ID)

        assert info["id"] == CHART_ID
        assert info["type"] == "barchart"
        assert info["title"] == "Sales by Region"
        assert info["subtitle"] == "FY25"
        assert info["dimensions"] == [{"field": "Region", "label": "Region", "library_id": None}]
        assert info["measures"] == [
            {"expression": "Sum(Sales)", "label": "Sales", "library_id": None},
            {"expression": None, "label": "Margin", "library_id": "measure-lib-1"},
        ]

    async def test_chart_data_reads_hypercube_layout(self):
        ws, session = _session()

        result = await session.get_object_data(CHART_ID, max_rows=100)

        assert result.headers == ["Region", "Sales", "Margin"]
        assert result.rows == [["East", "100", "0.2"], ["West", "50", "0.1"]]
        assert result.total_rows == 2
        assert result.truncated is False

    async def test_chart_data_reads_list_object(self):
        ws, session = _session()

        result = await session.get_object_data(LISTBOX_ID, max_rows=100)

        assert result.headers == ["Region"]
        assert result.rows == [["East"], ["West"]]

    async def test_unknown_object_raises(self):
        ws, session = _session()
        with pytest.raises(EngineError, match="not found"):
            await session.get_object_info("missing")


class TestAddToSheet:
    async def test_add_chart_appends_cell_below_existing_and_saves(self):
        ws, session = _session()

        result = await session.add_objects_to_sheet(SHEET_ID, [
            {"type": "kpi", "title": "Total", "dimensions": [], "measures": ["Sum(Sales)"]},
        ])

        assert result["object_count"] == 1
        set_props = ws.calls("SetProperties")[0]
        assert set_props["handle"] == 10
        cells = set_props["params"][0]["cells"]
        assert cells[0]["name"] == CHART_ID  # existing cell untouched
        new = cells[1]
        assert new["name"] == "child-1"
        assert new["type"] == "kpi"
        assert new["row"] >= 6  # placed below the existing 6-row chart
        assert ws.calls("DoSave")

    async def test_add_filter_pane_creates_listbox_children(self):
        ws, session = _session()

        result = await session.add_filter_pane(SHEET_ID, ["Region", "Year"], title="Filters")

        creates = ws.calls("CreateChild")
        pane = creates[0]["params"][0]
        assert creates[0]["handle"] == 10
        assert pane["qInfo"]["qType"] == "filterpane"
        assert pane["visualization"] == "filterpane"
        listboxes = creates[1:]
        assert [c["params"][0]["qListObjectDef"]["qDef"]["qFieldDefs"] for c in listboxes] == [["Region"], ["Year"]]
        assert all(c["handle"] == 31 for c in listboxes)  # children of the pane, not the sheet
        assert all(c["params"][0]["qInfo"]["qType"] == "listbox" for c in listboxes)
        assert result["filter_pane_id"] == "child-1"
        assert result["fields"] == ["Region", "Year"]
        assert ws.calls("DoSave")
