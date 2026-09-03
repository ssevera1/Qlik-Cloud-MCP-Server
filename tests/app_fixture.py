"""A fake Qlik app served over FakeWebSocket, rich enough for every engine tool.

Responses use the raw engine wire format: results wrapped by parameter name
(qLayout, qProp, qList, qReturn, qDataPages, qResult, qSuccess, qScript).
"""

from __future__ import annotations

from .fakes import FakeWebSocket

DOC = 1
APP_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

SHEET_ID = "sheet-1"
CHART_ID = "chart-1"
LISTBOX_ID = "listbox-1"

HANDLES = {
    SHEET_ID: 10,
    CHART_ID: 11,
    LISTBOX_ID: 12,
}
FIELD_HANDLE = 5
DIMENSION_HANDLE = 43
MEASURE_HANDLE = 44

SHEET_LAYOUT = {
    "qInfo": {"qId": SHEET_ID, "qType": "sheet"},
    "qMeta": {"title": "Overview", "description": "Main sheet", "published": True},
    "rank": 0,
    "cells": [
        {"name": CHART_ID, "type": "barchart", "col": 0, "row": 0, "colspan": 12, "rowspan": 6},
    ],
    "qChildList": {"qItems": [
        {"qInfo": {"qId": CHART_ID, "qType": "barchart"}, "qData": {"title": "Sales by Region"}},
    ]},
}

CHART_PROPERTIES = {
    "qInfo": {"qId": CHART_ID, "qType": "barchart"},
    "visualization": "barchart",
    "title": "Sales by Region",
    "subtitle": "FY25",
    "qHyperCubeDef": {
        "qDimensions": [{"qDef": {"qFieldDefs": ["Region"], "qFieldLabels": ["Region"]}}],
        "qMeasures": [
            {"qDef": {"qDef": "Sum(Sales)", "qLabel": "Sales"}},
            {"qLibraryId": "measure-lib-1"},
        ],
    },
}

CHART_LAYOUT = {
    "qInfo": {"qId": CHART_ID, "qType": "barchart"},
    "visualization": "barchart",
    "title": "Sales by Region",
    "qHyperCube": {
        "qDimensionInfo": [{"qFallbackTitle": "Region", "qCardinal": 2}],
        "qMeasureInfo": [{"qFallbackTitle": "Sales"}, {"qFallbackTitle": "Margin"}],
        "qSize": {"qcx": 3, "qcy": 2},
        "qDataPages": [{"qMatrix": [
            [{"qText": "East"}, {"qText": "100", "qNum": 100}, {"qText": "0.2", "qNum": 0.2}],
            [{"qText": "West"}, {"qText": "50", "qNum": 50}, {"qText": "0.1", "qNum": 0.1}],
        ]}],
    },
}

LISTBOX_LAYOUT = {
    "qInfo": {"qId": LISTBOX_ID, "qType": "listbox"},
    "title": "Region",
    "qListObject": {
        "qDimensionInfo": {"qFallbackTitle": "Region", "qCardinal": 2},
        "qSize": {"qcx": 1, "qcy": 2},
        "qDataPages": [{"qMatrix": [
            [{"qText": "East", "qState": "O", "qFrequency": "3"}],
            [{"qText": "West", "qState": "O", "qFrequency": "1"}],
        ]}],
    },
}

FIELD_VALUES_LAYOUT = {
    "qListObject": {
        "qDimensionInfo": {"qFallbackTitle": "Region", "qCardinal": 3},
        "qSize": {"qcx": 1, "qcy": 3},
        "qDataPages": [{"qMatrix": [
            [{"qText": "East", "qState": "S", "qFrequency": "10"}],
            [{"qText": "North", "qState": "O", "qFrequency": "4"}],
            [{"qText": "West", "qState": "X", "qFrequency": "1"}],
        ]}],
    },
}

MASTER_LISTS_LAYOUT = {
    "qDimensionList": {"qItems": [
        {"qInfo": {"qId": "dim-1", "qType": "dimension"},
         "qMeta": {"title": "Region", "description": "Sales region", "tags": ["geo"]},
         "qData": {"title": "Region", "qFieldDefs": ["Region"], "grouping": "N"}},
    ]},
    "qMeasureList": {"qItems": [
        {"qInfo": {"qId": "measure-lib-1", "qType": "measure"},
         "qMeta": {"title": "Margin", "description": "Gross margin", "tags": []},
         "qData": {"title": "Margin", "qDef": "Sum(Margin)/Sum(Sales)"}},
    ]},
    "qBookmarkList": {"qItems": [
        {"qInfo": {"qId": "bm-1", "qType": "bookmark"},
         "qMeta": {"title": "East only", "description": "", "createdDate": "2026-01-01"},
         "qData": {"sheetId": SHEET_ID, "selectionFields": "Region"}},
    ]},
}

SELECTIONS_LAYOUT = {
    "qSelectionObject": {
        "qBackCount": 1,
        "qForwardCount": 0,
        "qSelections": [{
            "qField": "Region",
            "qSelected": "East",
            "qSelectedCount": 1,
            "qTotal": 3,
            "qLocked": False,
            "qSelectedFieldSelectionInfo": [{"qName": "East"}],
        }],
    },
}

HYPERCUBE_LAYOUT = {
    "qHyperCube": {
        "qDimensionInfo": [{"qFallbackTitle": "Region"}],
        "qMeasureInfo": [{"qFallbackTitle": "Sum(Sales)"}],
        "qSize": {"qcx": 2, "qcy": 1},
        "qDataPages": [{"qMatrix": [[{"qText": "East"}, {"qText": "100", "qNum": 100}]]}],
    },
}

FIELD_LIST_LAYOUT = {
    "qFieldList": {"qItems": [
        {"qName": "Region", "qCardinal": 3, "qTags": ["$text"], "qSrcTables": ["Sales"]},
        {"qName": "Sales", "qCardinal": 40, "qTags": ["$numeric"], "qSrcTables": ["Sales"]},
    ]},
}

MEASURE_PROPERTIES = {
    "qInfo": {"qId": "measure-lib-1", "qType": "measure"},
    "qMeasure": {"qDef": "Sum(Margin)/Sum(Sales)", "qLabel": "Margin", "qGrouping": "N"},
    "qMetaDef": {"title": "Margin", "description": "Gross margin", "tags": []},
}

DIMENSION_PROPERTIES = {
    "qInfo": {"qId": "dim-1", "qType": "dimension"},
    "qDim": {"qGrouping": "N", "qFieldDefs": ["Region"], "qFieldLabels": ["Region"], "title": "Region"},
    "qMetaDef": {"title": "Region", "description": "Sales region", "tags": ["geo"]},
}

SEARCH_RESULT = {
    "qSearchGroupArray": [
        {"qId": 0, "qGroupType": "DatasetType", "qItems": [
            {"qItemType": "Field", "qIdentifier": "Region",
             "qItemMatches": [{"qText": "East"}, {"qText": "West"}]},
            {"qItemType": "Field", "qIdentifier": "City",
             "qItemMatches": [{"qText": "Easton"}]},
        ]},
    ],
    "qTotalNumberOfGroups": 1,
    "qTotalSearchTime": 3,
}

APP_LAYOUT = {
    "qTitle": "Sales Analysis",
    "qLastReloadTime": "2026-08-30T10:00:00.000Z",
    "qFileSize": 1234,
    "qModified": False,
}


def _copy(props: dict) -> dict:
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in props.items()}


def build_app_ws() -> FakeWebSocket:
    """A FakeWebSocket that behaves like a small app with one sheet and one chart."""
    state = {"last_object": None, "session_objects": {}, "next_session": 100, "children": 0}

    def generic(handle: int, generic_id: str) -> dict:
        return {"qInfo": {"qId": generic_id},
                "qReturn": {"qType": "GenericObject", "qHandle": handle, "qGenericId": generic_id}}

    def responder(msg: dict):
        method = msg["method"]
        handle = msg["handle"]
        params = msg["params"]

        # ── Global / Doc ──────────────────────────────────────────
        if method == "OpenDoc":
            return {"qReturn": {"qType": "Doc", "qHandle": DOC}}
        if method == "GetAppLayout":
            return {"qLayout": APP_LAYOUT}
        if method == "GetScript":
            return {"qScript": "LOAD * FROM Sales;"}
        if method in ("AddAlternateState", "RemoveAlternateState", "ClearAll", "SetProperties", "DoSave"):
            return {}
        if method in ("DestroySessionObject", "DestroyBookmark", "DestroyDimension", "DestroyMeasure"):
            return {"qSuccess": True}
        if method == "GetObjects":
            return {"qList": [{
                "qInfo": {"qId": SHEET_ID, "qType": "sheet"},
                "qMeta": SHEET_LAYOUT["qMeta"],
                "qData": {"rank": 0},
            }]}
        if method == "GetObject":
            obj_id = params[0]
            if obj_id not in HANDLES:
                return {"qReturn": {"qType": "Null", "qHandle": -1}}
            state["last_object"] = obj_id
            return {"qReturn": {"qType": "GenericObject", "qHandle": HANDLES[obj_id]}}
        if method == "GetField":
            return {"qReturn": {"qType": "Field", "qHandle": FIELD_HANDLE}}
        if method == "GetDimension":
            return {"qReturn": {"qType": "GenericDimension", "qHandle": DIMENSION_HANDLE}}
        if method == "GetMeasure":
            return {"qReturn": {"qType": "GenericMeasure", "qHandle": MEASURE_HANDLE}}
        if method == "CreateSessionObject":
            prop = params[0]
            h = state["next_session"]
            state["next_session"] += 1
            if "qListObjectDef" in prop:
                kind = "field_values"
            elif "qDimensionListDef" in prop or "qMeasureListDef" in prop or "qBookmarkListDef" in prop:
                kind = "master_lists"
            elif "qFieldListDef" in prop:
                kind = "fields"
            elif "qSelectionObjectDef" in prop:
                kind = "selections"
            else:
                kind = "hypercube"
            state["session_objects"][h] = kind
            return generic(h, f"session-{h}")
        if method == "CreateObject":
            return {"qReturn": {"qType": "GenericObject", "qHandle": 20, "qGenericId": "new-sheet"}}
        if method == "CreateBookmark":
            return generic(40, "bm-new")
        if method == "CreateDimension":
            return generic(41, "dim-new")
        if method == "CreateMeasure":
            return generic(42, "measure-new")
        if method == "SearchResults":
            return {"qResult": SEARCH_RESULT}
        if method == "ApplyBookmark":
            return {"qSuccess": params[0] == "bm-1"}

        # ── Field ─────────────────────────────────────────────────
        if method in ("SelectValues", "Select", "Clear"):
            return {"qReturn": True}

        # ── Generic objects ───────────────────────────────────────
        if method == "GetLayout":
            if handle == HANDLES[SHEET_ID]:
                return {"qLayout": SHEET_LAYOUT}
            if handle == HANDLES[CHART_ID]:
                return {"qLayout": CHART_LAYOUT}
            if handle == HANDLES[LISTBOX_ID]:
                return {"qLayout": LISTBOX_LAYOUT}
            kind = state["session_objects"].get(handle)
            layouts = {
                "field_values": FIELD_VALUES_LAYOUT,
                "master_lists": MASTER_LISTS_LAYOUT,
                "fields": FIELD_LIST_LAYOUT,
                "selections": SELECTIONS_LAYOUT,
                "hypercube": HYPERCUBE_LAYOUT,
            }
            return {"qLayout": layouts.get(kind, {})}
        if method == "GetProperties":
            if handle == HANDLES[CHART_ID]:
                return {"qProp": CHART_PROPERTIES}
            if handle == HANDLES[SHEET_ID]:
                return {"qProp": {"qInfo": SHEET_LAYOUT["qInfo"], "cells": list(SHEET_LAYOUT["cells"]),
                                  "columns": 24, "rows": 12}}
            if handle == MEASURE_HANDLE:
                return {"qProp": _copy(MEASURE_PROPERTIES)}
            if handle == DIMENSION_HANDLE:
                return {"qProp": _copy(DIMENSION_PROPERTIES)}
            return {"qProp": {}}
        if method == "GetHyperCubeData":
            return {"qDataPages": [{"qMatrix": []}]}
        if method == "GetListObjectData":
            return {"qDataPages": [{"qMatrix": []}]}
        if method == "SearchListObjectFor":
            return {"qSuccess": True}
        if method == "CreateChild":
            state["children"] += 1
            cid = f"child-{state['children']}"
            return generic(30 + state["children"], cid)
        return {}

    return FakeWebSocket(responder)
