"""Qlik Engine API client (WebSocket JSON-RPC).

Connects to the Qlik Associative Engine to perform hypercube
data retrieval, sheet inspection, and sheet creation.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import websockets
from websockets.asyncio.client import connect as ws_connect

from .auth import AuthManager
from .config import Config

logger = logging.getLogger(__name__)


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

        # Calculate column widths
        col_widths = [len(h) for h in self.headers]
        for row in self.rows[:100]:  # Limit for formatting
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        # Build table
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
        """Send a JSON-RPC request to the Engine and return the result."""
        request_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "handle": handle,
            "params": params or [],
        }

        await self._ws.send(json.dumps(msg))

        # Read responses until we get the one matching our request ID
        while True:
            raw = await self._ws.recv()
            response = json.loads(raw)
            if response.get("id") == request_id:
                if "error" in response:
                    err = response["error"]
                    raise EngineError(
                        f"Engine error: {err.get('message', 'Unknown')}",
                        code=err.get("code", -1),
                    )
                return response.get("result")

    async def get_sheets(self) -> list[dict]:
        """Get all sheets in the app."""
        result = await self._send("GetObjects", self._doc_handle, [
            {"qType": "sheet"}
        ])

        sheets = []
        for item in (result or []):
            obj_id = item.get("qInfo", {}).get("qId", "")
            # Get layout for each sheet
            try:
                layout = await self.get_object_layout(obj_id)
                sheets.append({
                    "id": obj_id,
                    "title": layout.get("qMeta", {}).get("title", ""),
                    "description": layout.get("qMeta", {}).get("description", ""),
                    "cells": self._extract_cells(layout),
                })
            except EngineError as e:
                logger.warning("Could not get layout for sheet %s: %s", obj_id, e)
                sheets.append({"id": obj_id, "title": "(error)", "cells": []})

        return sheets

    async def get_sheet_layout(self, sheet_id: str) -> dict:
        """Get the full layout of a specific sheet."""
        return await self.get_object_layout(sheet_id)

    async def get_object_layout(self, object_id: str) -> dict:
        """Get the layout of any object by ID."""
        # Get object handle
        result = await self._send("GetObject", self._doc_handle, [object_id])
        if result is None:
            raise EngineError(f"Object not found: {object_id}")

        obj_handle = result.get("qReturn", {}).get("qHandle")
        if obj_handle is None:
            raise EngineError(f"No handle returned for object: {object_id}")

        # Get layout
        layout = await self._send("GetLayout", obj_handle)
        return layout or {}

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

        Returns:
            HypercubeResult with headers and tabular data.
        """
        # Build hypercube definition
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
            {
                "qDef": {
                    "qDef": measure,
                    "qLabel": measure,
                },
            }
            for measure in measures
        ]

        initial_fetch = min(page_size, max_rows)

        obj_props = {
            "qInfo": {"qType": "hypercube"},
            "qHyperCubeDef": {
                "qDimensions": q_dimensions,
                "qMeasures": q_measures,
                "qInitialDataFetch": [
                    {
                        "qTop": 0,
                        "qLeft": 0,
                        "qHeight": initial_fetch,
                        "qWidth": len(dimensions) + len(measures),
                    }
                ],
            },
        }

        # Create session object
        result = await self._send("CreateSessionObject", self._doc_handle, [obj_props])
        if not result:
            raise EngineError("Failed to create session hypercube")

        obj_handle = result.get("qReturn", {}).get("qHandle")
        if obj_handle is None:
            raise EngineError("No handle for session hypercube")

        # Get layout with initial data
        layout = await self._send("GetLayout", obj_handle)
        if not layout:
            raise EngineError("Empty layout from hypercube")

        hc = layout.get("qHyperCube", {})
        dim_info = hc.get("qDimensionInfo", [])
        measure_info = hc.get("qMeasureInfo", [])

        # Build headers
        headers = [d.get("qFallbackTitle", f"Dim{i}") for i, d in enumerate(dim_info)]
        headers += [m.get("qFallbackTitle", f"Measure{i}") for i, m in enumerate(measure_info)]

        # Extract initial data
        data_pages = hc.get("qDataPages", [])
        rows = []
        for page in data_pages:
            for matrix_row in page.get("qMatrix", []):
                row = []
                for cell in matrix_row:
                    # Use text value if available, otherwise numeric
                    if cell.get("qText") is not None:
                        row.append(cell["qText"])
                    elif cell.get("qNum") is not None:
                        row.append(cell["qNum"])
                    else:
                        row.append(cell.get("qText", ""))
                rows.append(row)

        total_rows = hc.get("qSize", {}).get("qcy", len(rows))

        # Fetch additional pages if needed
        fetched = len(rows)
        while fetched < min(total_rows, max_rows):
            page_data = await self._send("GetHyperCubeData", obj_handle, [
                "/qHyperCubeDef",
                [
                    {
                        "qTop": fetched,
                        "qLeft": 0,
                        "qHeight": min(page_size, max_rows - fetched),
                        "qWidth": len(headers),
                    }
                ],
            ])

            if not page_data:
                break

            for page in page_data:
                for matrix_row in page.get("qMatrix", []):
                    row = []
                    for cell in matrix_row:
                        if cell.get("qText") is not None:
                            row.append(cell["qText"])
                        elif cell.get("qNum") is not None:
                            row.append(cell["qNum"])
                        else:
                            row.append("")
                    rows.append(row)

            new_fetched = len(rows)
            if new_fetched == fetched:
                break  # No more data
            fetched = new_fetched

        return HypercubeResult(
            headers=headers,
            rows=rows,
            total_rows=total_rows,
            truncated=len(rows) < total_rows,
        )

    async def apply_selections(self, field_name: str, values: list[str]) -> bool:
        """Apply a selection (filter) on a field."""
        # Get field handle
        result = await self._send("GetField", self._doc_handle, [field_name])
        if not result:
            raise EngineError(f"Field not found: {field_name}")

        field_handle = result.get("qReturn", {}).get("qHandle")
        if field_handle is None:
            raise EngineError(f"No handle for field: {field_name}")

        # Select values
        select_values = [{"qText": v} for v in values]
        await self._send("SelectValues", field_handle, [
            select_values, False, False
        ])
        return True

    async def clear_selections(self) -> None:
        """Clear all selections in the app."""
        await self._send("ClearAll", self._doc_handle, [])

    async def create_sheet(
        self, title: str, description: str = "", objects: Optional[list[dict]] = None
    ) -> dict:
        """Create a new sheet in the app.

        Args:
            title: Sheet title.
            description: Sheet description.
            objects: List of visualization definitions to add.

        Returns:
            Created sheet info with ID.
        """
        sheet_props = {
            "qInfo": {"qType": "sheet"},
            "qMeta": {
                "title": title,
                "description": description,
            },
            "qChildListDef": {
                "qData": {"title": "/title", "visualization": "/visualization"},
            },
            "cells": [],
            "rank": 0,
        }

        result = await self._send("CreateObject", self._doc_handle, [sheet_props])
        if not result:
            raise EngineError("Failed to create sheet")

        sheet_handle = result.get("qReturn", {}).get("qHandle")
        sheet_id = result.get("qReturn", {}).get("qGenericId", "")

        # Add child objects if provided
        obj_count = 0
        if objects and sheet_handle is not None:
            for i, obj_def in enumerate(objects):
                try:
                    child_props = self._build_child_props(obj_def, row=i)
                    await self._send("CreateChild", sheet_handle, [child_props])
                    obj_count += 1
                except EngineError as e:
                    logger.warning("Failed to create child object %d: %s", i, e)

        return {
            "sheet_id": sheet_id,
            "title": title,
            "object_count": obj_count,
        }

    async def close(self) -> None:
        """Close the WebSocket connection."""
        try:
            await self._ws.close()
        except Exception:
            pass

    @staticmethod
    def _extract_cells(layout: dict) -> list[dict]:
        """Extract visualization cells from a sheet layout."""
        cells = []
        for cell in layout.get("cells", []):
            cells.append({
                "name": cell.get("name", ""),
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
    def _build_child_props(obj_def: dict, row: int = 0) -> dict:
        """Build properties for a child visualization object."""
        vis_type = obj_def.get("type", "barchart")
        dimensions = obj_def.get("dimensions", [])
        measures = obj_def.get("measures", [])

        q_dimensions = [
            {"qDef": {"qFieldDefs": [d]}} for d in dimensions
        ]
        q_measures = [
            {"qDef": {"qDef": m, "qLabel": m}} for m in measures
        ]

        return {
            "qInfo": {"qType": vis_type},
            "qHyperCubeDef": {
                "qDimensions": q_dimensions,
                "qMeasures": q_measures,
            },
            "title": obj_def.get("title", ""),
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
        tenant_host = self.config.tenant_host
        ws_url = f"wss://{tenant_host}/app/{app_id}"
        headers = await self.auth.get_ws_headers()

        logger.debug("Connecting to Engine API: %s", ws_url)

        ws = await ws_connect(
            ws_url,
            additional_headers=headers,
            open_timeout=self.config.qlik.timeout_seconds,
            close_timeout=10,
        )

        try:
            # The doc handle for an opened app is conventionally -1 (global)
            # or we can use GetActiveDoc
            session = EngineSession(ws, doc_handle=-1, app_id=app_id)

            # Open the document to get the doc handle
            result = await session._send("OpenDoc", -1, [app_id])
            if result:
                doc_handle = result.get("qReturn", {}).get("qHandle", 1)
                session._doc_handle = doc_handle

            logger.debug("Engine session opened for app %s (handle=%d)", app_id, session._doc_handle)
            yield session

        finally:
            await session.close()
            logger.debug("Engine session closed for app %s", app_id)
