"""Qlik Engine API client (WebSocket JSON-RPC).

Connects to the Qlik Associative Engine (QIX) to perform hypercube
data retrieval, field discovery, sheet inspection, and sheet creation.

Wire format reference: https://qlik.dev/apis/json-rpc/qix/
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from websockets.asyncio.client import connect as ws_connect

from .auth import AuthManager
from .config import Config

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Maximum time (seconds) to wait for a single WebSocket message.
_WS_RECV_TIMEOUT = 120
# Maximum total time (seconds) to wait for the response to one request,
# regardless of how many unrelated notifications the engine pushes.
_WS_REQUEST_DEADLINE = 300

# Engine error text is echoed to the agent; keep it bounded.
_MAX_ERROR_LEN = 500

# Qlik Sense sheet grid (the standard 24 x 12 layout grid).
_SHEET_COLUMNS = 24
_SHEET_ROWS = 12

logger = logging.getLogger(__name__)


def _validate_id(value: str, label: str = "ID") -> str:
    """Validate that a value looks like a Qlik object identifier (UUID).

    Raises EngineError if the value is not a valid UUID, preventing
    path-traversal or injection via WebSocket URL construction.
    """
    if not value or not _UUID_RE.fullmatch(value):
        raise EngineError(f"Invalid {label}: expected UUID format")
    return value


class EngineError(Exception):
    """Raised when an Engine API call fails."""

    def __init__(self, message: str, code: int = -1) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class HypercubeResult:
    """Result from a hypercube data request."""

    headers: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    total_rows: int = 0
    truncated: bool = False

    def to_table(self) -> str:
        """Format as a readable text table."""
        if not self.headers or not self.rows:
            return "(no data)"

        col_widths = [len(h) for h in self.headers]
        for row in self.rows[:100]:  # Limit for formatting
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        header_line = " | ".join(
            h.ljust(col_widths[i]) for i, h in enumerate(self.headers)
        )
        separator = "-+-".join("-" * w for w in col_widths)

        lines = [header_line, separator]
        for row in self.rows:
            line = " | ".join(
                str(cell).ljust(col_widths[i]) if i < len(col_widths) else str(cell)
                for i, cell in enumerate(row)
            )
            lines.append(line)

        if self.truncated:
            lines.append(f"... (truncated, {self.total_rows} total rows)")

        return "\n".join(lines)

    def to_records(self) -> list[dict]:
        """Convert to list of dictionaries."""
        return [
            {self.headers[i]: cell for i, cell in enumerate(row) if i < len(self.headers)}
            for row in self.rows
        ]


def _cell_value(cell: dict) -> Any:
    """Pick the display value of a hypercube cell (text first, then number)."""
    if cell.get("qText") is not None:
        return cell["qText"]
    if cell.get("qNum") is not None:
        return cell["qNum"]
    return ""


def _rows_from_pages(pages: list[dict]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for page in pages or []:
        for matrix_row in page.get("qMatrix", []):
            rows.append([_cell_value(cell) for cell in matrix_row])
    return rows


class EngineSession:
    """A session connected to a specific Qlik app via the Engine API."""

    def __init__(self, ws: Any, doc_handle: int, app_id: str) -> None:
        self._ws = ws
        self._doc_handle = doc_handle
        self._app_id = app_id
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send(
        self, method: str, handle: int, params: Optional[list] = None
    ) -> Any:
        """Send a JSON-RPC request to the Engine and return the result.

        The engine interleaves id-less notifications (OnConnected, change
        lists, and so on) with responses. Those are skipped until the
        response carrying our request id arrives or the deadline passes.
        """
        request_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "handle": handle,
            "params": params or [],
        }

        await self._ws.send(json.dumps(msg))

        deadline = time.monotonic() + _WS_REQUEST_DEADLINE
        while True:
            if time.monotonic() > deadline:
                raise EngineError(
                    f"Engine request {method} timed out after {_WS_REQUEST_DEADLINE}s"
                )
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=_WS_RECV_TIMEOUT)
            except TimeoutError as e:
                raise EngineError(f"Engine request timed out after {_WS_RECV_TIMEOUT}s") from e
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Received non-JSON WebSocket message, skipping")
                continue
            if not isinstance(response, dict) or response.get("id") != request_id:
                continue
            if "error" in response:
                err = response["error"] or {}
                message = str(err.get("message", "Unknown"))[:_MAX_ERROR_LEN]
                raise EngineError(f"Engine error: {message}", code=err.get("code", -1))
            return response.get("result")

    # ── Sheets ────────────────────────────────────────────────────

    async def get_sheets(self) -> list[dict]:
        """Get all sheets in the app with their layout summary."""
        result = await self._send("GetObjects", self._doc_handle, [
            {"qTypes": ["sheet"], "qIncludeSessionObjects": False, "qData": {}}
        ])

        # GetObjects returns {"qList": [NxContainerEntry, ...]}.
        entries = result.get("qList", []) if isinstance(result, dict) else (result or [])

        sheets = []
        for item in entries:
            obj_id = item.get("qInfo", {}).get("qId", "")
            try:
                layout = await self.get_object_layout(obj_id)
                sheets.append({"id": obj_id, **self.describe_sheet(layout)})
            except EngineError as e:
                logger.warning("Could not get layout for sheet %s: %s", obj_id, e)
                sheets.append({"id": obj_id, "title": "(error)", "objects": [], "object_count": 0})

        return sheets

    async def get_sheet_layout(self, sheet_id: str) -> dict:
        """Get the full layout of a specific sheet."""
        return await self.get_object_layout(sheet_id)

    async def get_object_layout(self, object_id: str) -> dict:
        """Get the layout of any object by ID."""
        result = await self._send("GetObject", self._doc_handle, [object_id])
        if not result:
            raise EngineError(f"Object not found: {object_id}")

        obj_handle = result.get("qReturn", {}).get("qHandle")
        if obj_handle is None:
            raise EngineError(f"No handle returned for object: {object_id}")

        layout = await self._send("GetLayout", obj_handle)
        return layout or {}

    @classmethod
    def describe_sheet(cls, layout: dict) -> dict:
        """Summarize a sheet layout: title, description, and its objects."""
        meta = layout.get("qMeta", {}) or {}
        cells = cls._extract_cells(layout)

        # qChildList carries each child's id, type, and title. Cells carry
        # only id ("name") and grid placement, so merge the two by id.
        titles: dict[str, str] = {}
        child_list = layout.get("qChildList", {}) or {}
        for item in child_list.get("qItems", []) or []:
            child_id = item.get("qInfo", {}).get("qId", "")
            title = (item.get("qData", {}) or {}).get("title", "")
            if child_id:
                titles[child_id] = title if isinstance(title, str) else ""
        for cell in cells:
            cell["title"] = titles.get(cell["id"], "")

        return {
            "title": meta.get("title", "") or layout.get("title", ""),
            "description": meta.get("description", "") or layout.get("description", ""),
            "objects": cells,
            "object_count": len(cells),
        }

    # ── Hypercubes ────────────────────────────────────────────────

    async def create_hypercube(
        self,
        dimensions: list[str],
        measures: list[str],
        page_size: int = 1000,
        max_rows: int = 10000,
    ) -> HypercubeResult:
        """Create a session hypercube and fetch its data.

        Args:
            dimensions: Field names or expressions for dimensions.
            measures: Expressions for measures (e.g., "Sum(Revenue)").
            page_size: Rows per page for data fetching.
            max_rows: Maximum total rows to retrieve.
        """
        q_dimensions = [
            {
                "qDef": {
                    "qFieldDefs": [dim],
                    "qSortCriterias": [{"qSortByLoadOrder": 1}],
                },
            }
            for dim in dimensions
        ]
        q_measures = [
            {"qDef": {"qDef": measure, "qLabel": measure}} for measure in measures
        ]

        width = len(dimensions) + len(measures)
        initial_fetch = min(page_size, max_rows)

        obj_props = {
            "qInfo": {"qType": "hypercube"},
            "qHyperCubeDef": {
                "qDimensions": q_dimensions,
                "qMeasures": q_measures,
                "qInitialDataFetch": [
                    {"qTop": 0, "qLeft": 0, "qHeight": initial_fetch, "qWidth": width}
                ],
            },
        }

        result = await self._send("CreateSessionObject", self._doc_handle, [obj_props])
        if not result:
            raise EngineError("Failed to create session hypercube")

        obj_handle = result.get("qReturn", {}).get("qHandle")
        if obj_handle is None:
            raise EngineError("No handle for session hypercube")

        layout = await self._send("GetLayout", obj_handle)
        if not layout:
            raise EngineError("Empty layout from hypercube")

        hc = layout.get("qHyperCube", {})
        dim_info = hc.get("qDimensionInfo", [])
        measure_info = hc.get("qMeasureInfo", [])

        # Surface expression errors instead of returning silent empty data.
        problems = [
            d.get("qError", {}).get("qErrorCode") for d in dim_info if d.get("qError")
        ] + [
            m.get("qError", {}).get("qErrorCode") for m in measure_info if m.get("qError")
        ]
        if problems:
            raise EngineError(
                "Hypercube definition has invalid dimensions or measures "
                f"(engine error codes: {problems})"
            )

        headers = [d.get("qFallbackTitle", f"Dim{i}") for i, d in enumerate(dim_info)]
        headers += [m.get("qFallbackTitle", f"Measure{i}") for i, m in enumerate(measure_info)]

        rows = _rows_from_pages(hc.get("qDataPages", []))
        total_rows = hc.get("qSize", {}).get("qcy", len(rows))

        fetched = len(rows)
        while fetched < min(total_rows, max_rows):
            page_data = await self._send("GetHyperCubeData", obj_handle, [
                "/qHyperCubeDef",
                [{
                    "qTop": fetched,
                    "qLeft": 0,
                    "qHeight": min(page_size, max_rows - fetched),
                    "qWidth": len(headers),
                }],
            ])
            new_rows = _rows_from_pages(page_data or [])
            if not new_rows:
                break
            rows.extend(new_rows)
            fetched = len(rows)

        return HypercubeResult(
            headers=headers,
            rows=rows,
            total_rows=total_rows,
            truncated=len(rows) < total_rows,
        )

    # ── Fields ────────────────────────────────────────────────────

    async def get_fields(self) -> list[dict]:
        """List the user-visible fields of the app's data model."""
        result = await self._send("CreateSessionObject", self._doc_handle, [{
            "qInfo": {"qType": "FieldList"},
            "qFieldListDef": {
                "qShowSystem": False,
                "qShowHidden": False,
                "qShowDerivedFields": True,
                "qShowSemantic": True,
                "qShowSrcTables": True,
                "qShowImplicit": False,
            },
        }])
        if not result:
            raise EngineError("Failed to create field list")

        obj_handle = result.get("qReturn", {}).get("qHandle")
        if obj_handle is None:
            raise EngineError("No handle for field list")

        layout = await self._send("GetLayout", obj_handle) or {}
        items = (layout.get("qFieldList", {}) or {}).get("qItems", []) or []

        fields = []
        for item in items:
            if item.get("qIsSystem") or item.get("qIsHidden"):
                continue
            fields.append({
                "name": item.get("qName", ""),
                "cardinality": item.get("qCardinal", 0),
                "tags": item.get("qTags", []),
                "source_tables": item.get("qSrcTables", []),
            })
        return fields

    # ── Selections ────────────────────────────────────────────────

    async def apply_selections(self, field_name: str, values: list[str]) -> bool:
        """Apply a selection (filter) on a field.

        Returns the engine's success flag. False means none of the values
        matched, in which case the selection was not applied.
        """
        result = await self._send("GetField", self._doc_handle, [field_name])
        if not result:
            raise EngineError(f"Field not found: {field_name}")

        field_handle = result.get("qReturn", {}).get("qHandle")
        if field_handle is None:
            raise EngineError(f"No handle for field: {field_name}")

        select_values = [{"qText": v} for v in values]
        result = await self._send("SelectValues", field_handle, [
            select_values, False, False
        ])
        return bool((result or {}).get("qReturn", False))

    async def clear_selections(self) -> None:
        """Clear all selections in the app."""
        await self._send("ClearAll", self._doc_handle, [])

    # ── Sheet creation ────────────────────────────────────────────

    async def create_sheet(
        self, title: str, description: str = "", objects: Optional[list[dict]] = None
    ) -> dict:
        """Create a new sheet in the app, add visualizations, and save.

        The sheet is a persistent app object. Qlik Cloud only persists
        engine changes after DoSave, so the app is saved before returning.
        """
        sheet_props = {
            "qInfo": {"qType": "sheet"},
            "qMetaDef": {"title": title, "description": description},
            "title": title,
            "description": description,
            "columns": _SHEET_COLUMNS,
            "rows": _SHEET_ROWS,
            "cells": [],
            "rank": 0,
            "qChildListDef": {
                "qData": {
                    "title": "/title",
                    "visualization": "/visualization",
                    "description": "/description",
                },
            },
        }

        result = await self._send("CreateObject", self._doc_handle, [sheet_props])
        if not result:
            raise EngineError("Failed to create sheet")

        q_return = result.get("qReturn", {})
        sheet_handle = q_return.get("qHandle")
        sheet_id = q_return.get("qGenericId") or result.get("qInfo", {}).get("qId", "")

        created: list[dict] = []
        failed: list[str] = []
        if objects and sheet_handle is not None:
            for i, obj_def in enumerate(objects):
                try:
                    child_props = self._build_child_props(obj_def)
                    child = await self._send("CreateChild", sheet_handle, [child_props])
                    child_ret = (child or {}).get("qReturn", {})
                    child_id = child_ret.get("qGenericId") or (child or {}).get("qInfo", {}).get("qId")
                    if not child_id:
                        raise EngineError("Engine returned no id for child object")
                    created.append({"id": child_id, "type": child_props["qInfo"]["qType"]})
                except EngineError as e:
                    logger.warning("Failed to create child object %d: %s", i, e)
                    failed.append(f"{obj_def.get('title') or obj_def.get('type', '?')}: {e}")

        if created and sheet_handle is not None:
            props_result = await self._send("GetProperties", sheet_handle)
            props = (props_result or {}).get("qProp") or sheet_props
            props["cells"] = self._layout_cells(created)
            await self._send("SetProperties", sheet_handle, [props])

        await self._send("DoSave", self._doc_handle)

        return {
            "sheet_id": sheet_id,
            "title": title,
            "object_count": len(created),
            "objects": created,
            "failed_objects": failed,
            "saved": True,
        }

    async def close(self) -> None:
        """Close the WebSocket connection."""
        try:
            await self._ws.close()
        except Exception as e:  # noqa: BLE001 - closing is best effort
            logger.debug("Ignoring error while closing Engine WebSocket: %s", e)

    @staticmethod
    def _extract_cells(layout: dict) -> list[dict]:
        """Extract visualization cells (grid placement) from a sheet layout."""
        cells = []
        for cell in layout.get("cells", []) or []:
            name = cell.get("name", "")
            cells.append({
                "id": name,
                "name": name,
                "type": cell.get("type", ""),
                "bounds": {
                    "x": cell.get("col", 0),
                    "y": cell.get("row", 0),
                    "width": cell.get("colspan", 1),
                    "height": cell.get("rowspan", 1),
                },
            })
        return cells

    @staticmethod
    def _layout_cells(created: list[dict]) -> list[dict]:
        """Arrange created objects on the 24 x 12 sheet grid."""
        count = len(created)
        if count <= 1:
            per_row = 1
        elif count <= 4:
            per_row = 2
        elif count <= 9:
            per_row = 3
        else:
            per_row = 4
        rows_needed = max(1, math.ceil(count / per_row))
        colspan = _SHEET_COLUMNS // per_row
        rowspan = max(1, _SHEET_ROWS // rows_needed)

        cells = []
        for i, obj in enumerate(created):
            col = (i % per_row) * colspan
            row = (i // per_row) * rowspan
            cells.append({
                "name": obj["id"],
                "type": obj["type"],
                "col": col,
                "row": row,
                "colspan": colspan,
                "rowspan": rowspan,
                "bounds": {
                    "x": col / _SHEET_COLUMNS * 100,
                    "y": row / _SHEET_ROWS * 100,
                    "width": colspan / _SHEET_COLUMNS * 100,
                    "height": rowspan / _SHEET_ROWS * 100,
                },
            })
        return cells

    @staticmethod
    def _build_child_props(obj_def: dict, row: int = 0) -> dict:
        """Build properties for a child visualization object."""
        vis_type = obj_def.get("type", "barchart")
        dimensions = obj_def.get("dimensions", [])
        measures = obj_def.get("measures", [])

        q_dimensions = [{"qDef": {"qFieldDefs": [d]}} for d in dimensions]
        q_measures = [{"qDef": {"qDef": m, "qLabel": m}} for m in measures]

        return {
            "qInfo": {"qType": vis_type},
            "visualization": vis_type,
            "title": obj_def.get("title", ""),
            "showTitles": True,
            "qHyperCubeDef": {
                "qDimensions": q_dimensions,
                "qMeasures": q_measures,
                "qInitialDataFetch": [
                    {"qTop": 0, "qLeft": 0, "qHeight": 50, "qWidth": max(1, len(dimensions) + len(measures))}
                ],
            },
        }


class EngineClient:
    """Factory for Engine API sessions."""

    def __init__(self, config: Config, auth: AuthManager) -> None:
        self.config = config
        self.auth = auth

    @asynccontextmanager
    async def open_app(self, app_id: str) -> AsyncIterator[EngineSession]:
        """Open a WebSocket connection to a Qlik app.

        Usage:
            async with engine_client.open_app("app-id") as session:
                sheets = await session.get_sheets()
        """
        _validate_id(app_id, "app_id")

        tenant_host = self.config.tenant_host
        ws_url = f"wss://{tenant_host}/app/{app_id}"
        headers = await self.auth.get_ws_headers()

        logger.debug("Connecting to Engine API: %s", ws_url)

        try:
            ws = await ws_connect(
                ws_url,
                additional_headers=headers,
                open_timeout=self.config.qlik.timeout_seconds,
                close_timeout=10,
                max_size=None,
            )
        except Exception as e:
            raise EngineError(f"Failed to connect to Engine API: {e}") from e

        session = EngineSession(ws, doc_handle=-1, app_id=app_id)
        try:
            # Global handle is -1; OpenDoc returns the document handle.
            result = await session._send("OpenDoc", -1, [app_id])
            if not result:
                raise EngineError(f"Failed to open document: {app_id}")
            doc_handle = result.get("qReturn", {}).get("qHandle")
            if doc_handle is None:
                raise EngineError(f"No document handle returned for: {app_id}")
            session._doc_handle = doc_handle

            logger.debug("Engine session opened for app %s (handle=%d)", app_id, doc_handle)
            yield session
        finally:
            await session.close()
            logger.debug("Engine session closed for app %s", app_id)
