"""Qlik Engine API client (WebSocket JSON-RPC).

Connects to the Qlik Associative Engine (QIX) to read the data model,
inspect and build sheets, manage master items and bookmarks, apply
selections, and compute governed data.

Wire format reference: https://qlik.dev/apis/json-rpc/qix/

Result shapes: the raw JSON-RPC engine wraps every return value under its
documented parameter name, for example ``{"qLayout": {...}}`` for GetLayout
and ``{"qDataPages": [...]}`` for GetHyperCubeData. Client libraries such as
enigma.js unwrap these; this client does it itself via ``_unwrap``.

Sessions: ``EngineClient`` keeps one WebSocket per app open between tool
calls (see ``qlik.reuse_sessions``). Calls to the same app are serialized on
that socket; different apps run in parallel. Selections made with the
selection tools persist on the app's session, while ``filters`` passed to
data tools are applied in a temporary alternate state so they never leak.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import re
import time
import uuid
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
# Size of objects appended to an existing sheet (half width, a third height).
_APPEND_COLSPAN = 12
_APPEND_ROWSPAN = 4

# Engine data pages are capped at 10,000 cells.
_MAX_PAGE_CELLS = 10000

# qState codes on list object cells.
_STATE_NAMES = {
    "S": "selected", "O": "optional", "X": "excluded", "A": "alternative",
    "L": "locked", "XS": "selected_excluded", "XL": "locked_excluded", "D": "deselected",
}

logger = logging.getLogger(__name__)


def _validate_id(value: str, label: str = "ID") -> str:
    """Validate that a value looks like a Qlik object identifier (UUID).

    Raises EngineError if the value is not a valid UUID, preventing
    path-traversal or injection via WebSocket URL construction.
    """
    if not value or not _UUID_RE.fullmatch(value):
        raise EngineError(f"Invalid {label}: expected UUID format")
    return value


def _unwrap(result: Any, key: str) -> Any:
    """Return ``result[key]`` when the engine wrapped the value, else ``result``."""
    if isinstance(result, dict) and key in result:
        return result[key]
    return result


class EngineError(Exception):
    """Raised when an Engine API call fails."""

    def __init__(self, message: str, code: int = -1) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class HypercubeResult:
    """Tabular result from a hypercube or list object."""

    headers: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    total_rows: int = 0
    truncated: bool = False
    unmatched_filters: list[dict] = field(default_factory=list)

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

    def to_markdown(self) -> str:
        """Format as a GitHub-flavored markdown table."""
        if not self.headers:
            return "(no data)"

        def esc(value: Any) -> str:
            return str(value).replace("|", "\\|").replace("\n", " ")

        lines = ["| " + " | ".join(esc(h) for h in self.headers) + " |",
                 "|" + "|".join(" --- " for _ in self.headers) + "|"]
        for row in self.rows:
            lines.append("| " + " | ".join(esc(c) for c in row) + " |")
        if self.truncated:
            lines.append(f"\n(truncated: showing {len(self.rows)} of {self.total_rows} rows)")
        return "\n".join(lines)

    def to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(self.headers)
        writer.writerows(self.rows)
        return buf.getvalue()

    def to_records(self) -> list[dict]:
        """Convert to list of dictionaries."""
        return [
            {self.headers[i]: cell for i, cell in enumerate(row) if i < len(self.headers)}
            for row in self.rows
        ]

    def as_payload(self, fmt: str = "json") -> dict:
        """The shape tools return to agents.

        ``json`` (default) returns ``columns`` and ``rows`` (compact and
        exact), ``markdown`` returns a table for display, ``csv`` returns
        text ready to save.
        """
        payload: dict = {
            "row_count": len(self.rows),
            "total_rows": self.total_rows,
            "truncated": self.truncated,
        }
        if fmt == "markdown":
            payload["table"] = self.to_markdown()
        elif fmt == "csv":
            payload["csv"] = self.to_csv()
        else:
            payload["columns"] = self.headers
            payload["rows"] = self.rows
        if self.unmatched_filters:
            payload["filters_not_matched"] = self.unmatched_filters
        return payload


def _cell_value(cell: dict) -> Any:
    """Pick the display value of a data cell (text first, then number)."""
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


def _frequency(text: Any) -> Optional[int]:
    try:
        return int(str(text))
    except (TypeError, ValueError):
        return None


def _generic_id(result: Any) -> str:
    """Id of an object returned by a Create* call (qReturn.qGenericId or qInfo.qId)."""
    result = result or {}
    return ((result.get("qReturn") or {}).get("qGenericId")
            or (result.get("qInfo") or {}).get("qId") or "")


def _generic_handle(result: Any) -> Optional[int]:
    return ((result or {}).get("qReturn") or {}).get("qHandle")


class EngineSession:
    """A session connected to a specific Qlik app via the Engine API."""

    def __init__(self, ws: Any, doc_handle: int, app_id: str) -> None:
        self._ws = ws
        self._doc_handle = doc_handle
        self._app_id = app_id
        self._request_id = 0
        self._temp_objects: list[str] = []
        self._temp_states: list[str] = []
        self.broken = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send(
        self, method: str, handle: int, params: Optional[list] = None
    ) -> Any:
        """Send a JSON-RPC request to the Engine and return the raw result.

        The engine interleaves id-less notifications (OnConnected, change
        lists, and so on) with responses. Those are skipped until the
        response carrying our request id arrives or the deadline passes.
        Transport failures mark the session broken so a pooled socket is
        not reused.
        """
        request_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "handle": handle,
            "params": params or [],
        }

        try:
            await self._ws.send(json.dumps(msg))
        except Exception as e:  # noqa: BLE001 - any socket failure means the session is dead
            self.broken = True
            raise EngineError(f"Engine connection lost while sending {method}: {e}") from e

        deadline = time.monotonic() + _WS_REQUEST_DEADLINE
        while True:
            if time.monotonic() > deadline:
                self.broken = True
                raise EngineError(
                    f"Engine request {method} timed out after {_WS_REQUEST_DEADLINE}s"
                )
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=_WS_RECV_TIMEOUT)
            except TimeoutError as e:
                self.broken = True
                raise EngineError(f"Engine request timed out after {_WS_RECV_TIMEOUT}s") from e
            except Exception as e:  # noqa: BLE001
                self.broken = True
                raise EngineError(f"Engine connection lost while waiting for {method}: {e}") from e
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

    # ── Handles, layouts, temporary objects ───────────────────────

    async def _get_object_handle(self, object_id: str) -> int:
        result = await self._send("GetObject", self._doc_handle, [object_id])
        ret = (result or {}).get("qReturn") or {}
        handle = ret.get("qHandle")
        if handle is None or handle < 0 or ret.get("qType") == "Null":
            raise EngineError(f"Object not found: {object_id}")
        return handle

    async def _get_field_handle(self, field_name: str, state: Optional[str] = None) -> int:
        params: list = [field_name] if state is None else [field_name, state]
        result = await self._send("GetField", self._doc_handle, params)
        handle = _generic_handle(result)
        if handle is None or handle < 0:
            raise EngineError(f"Field not found: {field_name}")
        return handle

    async def _layout(self, handle: int) -> dict:
        return _unwrap(await self._send("GetLayout", handle), "qLayout") or {}

    async def _properties(self, handle: int) -> dict:
        return _unwrap(await self._send("GetProperties", handle), "qProp") or {}

    async def _create_session_object(self, props: dict) -> int:
        result = await self._send("CreateSessionObject", self._doc_handle, [props])
        handle = _generic_handle(result)
        if handle is None:
            raise EngineError(
                f"Failed to create session object of type {(props.get('qInfo') or {}).get('qType')}"
            )
        obj_id = _generic_id(result)
        if obj_id:
            self._temp_objects.append(obj_id)
        return handle

    async def _add_alternate_state(self) -> str:
        name = f"mcp_{uuid.uuid4().hex[:8]}"
        await self._send("AddAlternateState", self._doc_handle, [name])
        self._temp_states.append(name)
        return name

    async def _remove_alternate_state(self, name: str) -> None:
        try:
            await self._send("RemoveAlternateState", self._doc_handle, [name])
        except EngineError as e:
            logger.debug("Could not remove alternate state %s: %s", name, e)
        if name in self._temp_states:
            self._temp_states.remove(name)

    async def cleanup_temp(self) -> None:
        """Destroy session objects and alternate states created during a tool call.

        Only needed when the socket is kept open between calls; a closed
        session takes its session objects with it.
        """
        if self.broken:
            self._temp_objects.clear()
            self._temp_states.clear()
            return
        for obj_id in list(self._temp_objects):
            try:
                await self._send("DestroySessionObject", self._doc_handle, [obj_id])
            except EngineError as e:
                logger.debug("Could not destroy session object %s: %s", obj_id, e)
        self._temp_objects.clear()
        for name in list(self._temp_states):
            await self._remove_alternate_state(name)

    async def _save(self) -> None:
        await self._send("DoSave", self._doc_handle)

    async def get_app_layout(self) -> dict:
        """App-level layout: title, last reload time, file size, and so on."""
        return _unwrap(await self._send("GetAppLayout", self._doc_handle), "qLayout") or {}

    async def get_script(self) -> str:
        """The app's load script."""
        result = await self._send("GetScript", self._doc_handle)
        script = _unwrap(result, "qScript")
        return script if isinstance(script, str) else ""

    # ── Sheets ────────────────────────────────────────────────────

    async def _sheet_entries(self) -> list[dict]:
        result = await self._send("GetObjects", self._doc_handle, [
            {"qTypes": ["sheet"], "qIncludeSessionObjects": False, "qData": {"rank": "/rank"}}
        ])
        # GetObjects returns {"qList": [NxContainerEntry, ...]}.
        entries = _unwrap(result, "qList")
        return entries if isinstance(entries, list) else []

    async def list_sheets(self) -> list[dict]:
        """Cheap sheet listing from the container entries (no per-sheet layout calls)."""
        sheets = []
        for item in await self._sheet_entries():
            meta = item.get("qMeta") or {}
            data = item.get("qData") or {}
            rank = data.get("rank", 0)
            sheets.append({
                "id": (item.get("qInfo") or {}).get("qId", ""),
                "title": meta.get("title", "") or "",
                "description": meta.get("description", "") or "",
                "published": bool(meta.get("published", False)),
                "rank": rank if isinstance(rank, (int, float)) and not isinstance(rank, bool) else 0,
            })
        return sheets

    async def get_sheets(self) -> list[dict]:
        """Get all sheets in the app with their layout summary."""
        sheets = []
        for item in await self._sheet_entries():
            obj_id = (item.get("qInfo") or {}).get("qId", "")
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
        return await self._layout(await self._get_object_handle(object_id))

    @classmethod
    def describe_sheet(cls, layout: dict) -> dict:
        """Summarize a sheet layout: title, description, and its objects."""
        meta = layout.get("qMeta") or {}
        cells = cls._extract_cells(layout)

        # qChildList carries each child's id, type, and title. Cells carry
        # only id ("name") and grid placement, so merge the two by id.
        titles: dict[str, str] = {}
        child_list = layout.get("qChildList") or {}
        for item in child_list.get("qItems") or []:
            child_id = (item.get("qInfo") or {}).get("qId", "")
            title = (item.get("qData") or {}).get("title", "")
            if child_id:
                titles[child_id] = title if isinstance(title, str) else ""
        for cell in cells:
            cell["title"] = titles.get(cell["id"], "")

        return {
            "title": meta.get("title", "") or layout.get("title", "") or "",
            "description": meta.get("description", "") or layout.get("description", "") or "",
            "objects": cells,
            "object_count": len(cells),
        }

    # ── Fields and values ─────────────────────────────────────────

    async def get_fields(self) -> list[dict]:
        """List the user-visible fields of the app's data model."""
        handle = await self._create_session_object({
            "qInfo": {"qType": "FieldList"},
            "qFieldListDef": {
                "qShowSystem": False,
                "qShowHidden": False,
                "qShowDerivedFields": True,
                "qShowSemantic": True,
                "qShowSrcTables": True,
                "qShowImplicit": False,
            },
        })
        layout = await self._layout(handle)
        items = (layout.get("qFieldList") or {}).get("qItems") or []

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

    async def get_field_values(
        self, field_name: str, max_values: int = 100, match: Optional[str] = None
    ) -> dict:
        """Distinct values of a field with selection state and frequency."""
        height = max(1, min(max_values, _MAX_PAGE_CELLS))
        handle = await self._create_session_object({
            "qInfo": {"qType": "ListObject"},
            "qListObjectDef": {
                "qDef": {
                    "qFieldDefs": [field_name],
                    "qSortCriterias": [{"qSortByState": 1, "qSortByFrequency": -1, "qSortByAscii": 1}],
                },
                "qShowAlternatives": True,
                "qFrequencyMode": "V",
                "qInitialDataFetch": [{"qTop": 0, "qLeft": 0, "qHeight": height, "qWidth": 1}],
            },
        })
        if match:
            await self._send("SearchListObjectFor", handle, ["/qListObjectDef", match])

        layout = await self._layout(handle)
        list_object = layout.get("qListObject") or {}
        dim_info = list_object.get("qDimensionInfo") or {}
        if dim_info.get("qError"):
            raise EngineError(f"Field not found: {field_name}")

        values = []
        for page in list_object.get("qDataPages") or []:
            for row in page.get("qMatrix") or []:
                if not row:
                    continue
                cell = row[0]
                values.append({
                    "value": _cell_value(cell),
                    "state": _STATE_NAMES.get(cell.get("qState", "O"), cell.get("qState", "O")),
                    "frequency": _frequency(cell.get("qFrequency")),
                })

        total = (list_object.get("qSize") or {}).get("qcy", len(values))
        return {
            "field": field_name,
            "total_values": total,
            "returned": len(values),
            "truncated": len(values) < total,
            "values": values,
        }

    async def search_field_values(
        self, terms: list[str], fields: Optional[list[str]] = None, max_matches: int = 10
    ) -> dict:
        """Smart-search the data for terms, grouped by the fields where they match."""
        options = {"qSearchFields": list(fields or []), "qContext": "CurrentSelections"}
        page = {
            "qOffset": 0,
            "qCount": 100,
            "qMaxNbrFieldMatches": max(1, max_matches),
            "qGroupOptions": [{"qGroupType": "DatasetType", "qOffset": 0, "qCount": -1}],
            "qGroupItemOptions": [{"qGroupItemType": "Field", "qOffset": 0, "qCount": -1}],
        }
        raw = await self._send("SearchResults", self._doc_handle, [options, terms, page])
        result = _unwrap(raw, "qResult") or {}

        matches = []
        for group in result.get("qSearchGroupArray") or []:
            for item in group.get("qItems") or []:
                matches.append({
                    "field": item.get("qIdentifier", ""),
                    "values": [m.get("qText", "") for m in item.get("qItemMatches") or []],
                })
        return {
            "terms": terms,
            "matches": matches,
            "total_groups": result.get("qTotalNumberOfGroups", len(matches)),
        }

    # ── Selections ────────────────────────────────────────────────

    async def apply_selections(
        self, field_name: str, values: list[str], state: Optional[str] = None
    ) -> bool:
        """Select exact values in a field (optionally in an alternate state).

        Returns the engine's success flag. False means none of the values
        matched, in which case the selection was not applied.
        """
        field_handle = await self._get_field_handle(field_name, state)
        select_values = [{"qText": v} for v in values]
        result = await self._send("SelectValues", field_handle, [select_values, False, False])
        return bool((result or {}).get("qReturn", False))

    async def select_values(
        self,
        field_name: str,
        values: Optional[list[str]] = None,
        match: Optional[str] = None,
        toggle: bool = False,
    ) -> dict:
        """Select values in the session's default state, by exact values or a search pattern.

        ``match`` uses Qlik search syntax: wildcards (``Ea*``), numeric ranges
        (``>100``), or plain text for a contains-match.
        """
        if not values and not match:
            raise EngineError("Provide values or match to select")
        field_handle = await self._get_field_handle(field_name)
        if values:
            result = await self._send(
                "SelectValues", field_handle, [[{"qText": v} for v in values], toggle, False]
            )
            mode = "values"
        else:
            result = await self._send("Select", field_handle, [match, False, 0])
            mode = "pattern"
        applied = bool((result or {}).get("qReturn", False))
        return {"field": field_name, "mode": mode, "applied": applied}

    async def clear_selections(self, fields: Optional[list[str]] = None) -> dict:
        """Clear all selections, or only the given fields."""
        if not fields:
            await self._send("ClearAll", self._doc_handle, [])
            return {"cleared": "all"}
        cleared = []
        for name in fields:
            field_handle = await self._get_field_handle(name)
            await self._send("Clear", field_handle, [])
            cleared.append(name)
        return {"cleared": cleared}

    async def get_current_selections(self) -> list[dict]:
        """Selections active in the session's default state."""
        handle = await self._create_session_object({
            "qInfo": {"qType": "CurrentSelection"},
            "qSelectionObjectDef": {},
        })
        layout = await self._layout(handle)
        selections = []
        for item in (layout.get("qSelectionObject") or {}).get("qSelections") or []:
            names = [i.get("qName", "") for i in item.get("qSelectedFieldSelectionInfo") or []]
            if not names and item.get("qSelected"):
                names = [v.strip() for v in str(item["qSelected"]).split(", ") if v.strip()]
            selections.append({
                "field": item.get("qField", ""),
                "selected": names,
                "selected_count": item.get("qSelectedCount", len(names)),
                "total": item.get("qTotal", 0),
                "locked": bool(item.get("qLocked", False)),
            })
        return selections

    # ── Master items and bookmarks ────────────────────────────────

    async def get_master_items(self) -> dict:
        """Library (master) dimensions and measures."""
        handle = await self._create_session_object({
            "qInfo": {"qType": "MasterItemLists"},
            "qDimensionListDef": {
                "qType": "dimension",
                "qData": {
                    "title": "/qMetaDef/title",
                    "description": "/qMetaDef/description",
                    "tags": "/qMetaDef/tags",
                    "qFieldDefs": "/qDim/qFieldDefs",
                    "qFieldLabels": "/qDim/qFieldLabels",
                    "grouping": "/qDim/qGrouping",
                    "labelExpression": "/qDim/qLabelExpression",
                },
            },
            "qMeasureListDef": {
                "qType": "measure",
                "qData": {
                    "title": "/qMetaDef/title",
                    "description": "/qMetaDef/description",
                    "tags": "/qMetaDef/tags",
                    "qDef": "/qMeasure/qDef",
                    "qLabel": "/qMeasure/qLabel",
                    "labelExpression": "/qMeasure/qLabelExpression",
                },
            },
        })
        layout = await self._layout(handle)

        dimensions = []
        for item in (layout.get("qDimensionList") or {}).get("qItems") or []:
            meta = item.get("qMeta") or {}
            data = item.get("qData") or {}
            dimensions.append({
                "id": (item.get("qInfo") or {}).get("qId", ""),
                "title": meta.get("title") or data.get("title") or "",
                "description": meta.get("description") or data.get("description") or "",
                "tags": meta.get("tags") or data.get("tags") or [],
                "field_defs": data.get("qFieldDefs") or [],
                "grouping": data.get("grouping") or "N",
            })

        measures = []
        for item in (layout.get("qMeasureList") or {}).get("qItems") or []:
            meta = item.get("qMeta") or {}
            data = item.get("qData") or {}
            measures.append({
                "id": (item.get("qInfo") or {}).get("qId", ""),
                "title": meta.get("title") or data.get("title") or "",
                "description": meta.get("description") or data.get("description") or "",
                "tags": meta.get("tags") or data.get("tags") or [],
                "expression": data.get("qDef") or "",
            })

        return {"dimensions": dimensions, "measures": measures}

    async def create_dimension(
        self,
        title: str,
        field_defs: list[str],
        label: Optional[str] = None,
        description: str = "",
        tags: Optional[list[str]] = None,
        grouping: str = "N",
    ) -> dict:
        """Create a master dimension and save the app."""
        props = {
            "qInfo": {"qType": "dimension"},
            "qDim": {
                "qGrouping": grouping,
                "qFieldDefs": list(field_defs),
                "qFieldLabels": [label or ""] * len(field_defs),
                "title": title,
                "qLabelExpression": "",
            },
            "qMetaDef": {"title": title, "description": description, "tags": list(tags or [])},
        }
        result = await self._send("CreateDimension", self._doc_handle, [props])
        new_id = _generic_id(result)
        if not new_id:
            raise EngineError("Engine returned no id for the new dimension")
        await self._save()
        return {"id": new_id, "title": title, "field_defs": list(field_defs), "saved": True}

    async def create_measure(
        self,
        title: str,
        expression: str,
        label: Optional[str] = None,
        description: str = "",
        tags: Optional[list[str]] = None,
    ) -> dict:
        """Create a master measure and save the app."""
        props = {
            "qInfo": {"qType": "measure"},
            "qMeasure": {
                "qLabel": label or title,
                "qDef": expression,
                "qGrouping": "N",
                "qExpressions": [],
                "qActiveExpression": 0,
                "qLabelExpression": "",
            },
            "qMetaDef": {"title": title, "description": description, "tags": list(tags or [])},
        }
        result = await self._send("CreateMeasure", self._doc_handle, [props])
        new_id = _generic_id(result)
        if not new_id:
            raise EngineError("Engine returned no id for the new measure")
        await self._save()
        return {"id": new_id, "title": title, "expression": expression, "saved": True}

    async def _update_master_item(self, getter: str, item_id: str, mutate) -> dict:
        result = await self._send(getter, self._doc_handle, [item_id])
        handle = _generic_handle(result)
        if handle is None or handle < 0:
            raise EngineError(f"Master item not found: {item_id}")
        props = await self._properties(handle)
        if not props:
            raise EngineError(f"Master item has no properties: {item_id}")
        mutate(props)
        await self._send("SetProperties", handle, [props])
        await self._save()
        return {"id": item_id, "saved": True}

    async def update_dimension(
        self,
        dimension_id: str,
        title: Optional[str] = None,
        field_defs: Optional[list[str]] = None,
        label: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        def mutate(props: dict) -> None:
            dim = props.setdefault("qDim", {})
            meta = props.setdefault("qMetaDef", {})
            if field_defs is not None:
                dim["qFieldDefs"] = list(field_defs)
                dim["qFieldLabels"] = [label or ""] * len(field_defs)
            elif label is not None:
                dim["qFieldLabels"] = [label] * max(1, len(dim.get("qFieldDefs") or [1]))
            if title is not None:
                dim["title"] = title
                meta["title"] = title
            if description is not None:
                meta["description"] = description
            if tags is not None:
                meta["tags"] = list(tags)

        return await self._update_master_item("GetDimension", dimension_id, mutate)

    async def update_measure(
        self,
        measure_id: str,
        title: Optional[str] = None,
        expression: Optional[str] = None,
        label: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        def mutate(props: dict) -> None:
            measure = props.setdefault("qMeasure", {})
            meta = props.setdefault("qMetaDef", {})
            if expression is not None:
                measure["qDef"] = expression
            if label is not None:
                measure["qLabel"] = label
            if title is not None:
                meta["title"] = title
            if description is not None:
                meta["description"] = description
            if tags is not None:
                meta["tags"] = list(tags)

        return await self._update_master_item("GetMeasure", measure_id, mutate)

    async def delete_dimension(self, dimension_id: str) -> bool:
        result = await self._send("DestroyDimension", self._doc_handle, [dimension_id])
        ok = bool((result or {}).get("qSuccess", False))
        if ok:
            await self._save()
        return ok

    async def delete_measure(self, measure_id: str) -> bool:
        result = await self._send("DestroyMeasure", self._doc_handle, [measure_id])
        ok = bool((result or {}).get("qSuccess", False))
        if ok:
            await self._save()
        return ok

    async def get_bookmarks(self) -> list[dict]:
        """Bookmarks stored in the app."""
        handle = await self._create_session_object({
            "qInfo": {"qType": "BookmarkList"},
            "qBookmarkListDef": {
                "qType": "bookmark",
                "qData": {
                    "title": "/qMetaDef/title",
                    "description": "/qMetaDef/description",
                    "sheetId": "/sheetId",
                    "selectionFields": "/selectionFields",
                    "creationDate": "/creationDate",
                },
            },
        })
        layout = await self._layout(handle)

        bookmarks = []
        for item in (layout.get("qBookmarkList") or {}).get("qItems") or []:
            meta = item.get("qMeta") or {}
            data = item.get("qData") or {}
            bookmarks.append({
                "id": (item.get("qInfo") or {}).get("qId", ""),
                "title": meta.get("title") or data.get("title") or "",
                "description": meta.get("description") or data.get("description") or "",
                "sheet_id": data.get("sheetId") or "",
                "selection_fields": data.get("selectionFields") or "",
                "created": meta.get("createdDate") or data.get("creationDate") or "",
            })
        return bookmarks

    async def apply_bookmark(self, bookmark_id: str) -> bool:
        """Apply a bookmark's selections to this session. Returns the engine's success flag."""
        result = await self._send("ApplyBookmark", self._doc_handle, [bookmark_id])
        return bool((result or {}).get("qSuccess", False))

    async def create_bookmark(self, title: str, description: str = "", sheet_id: str = "") -> dict:
        """Save the session's current selections as a bookmark and save the app."""
        props: dict = {
            "qInfo": {"qType": "bookmark"},
            "qMetaDef": {"title": title, "description": description},
            "creationDate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if sheet_id:
            props["sheetId"] = sheet_id
        result = await self._send("CreateBookmark", self._doc_handle, [props])
        new_id = _generic_id(result)
        if not new_id:
            raise EngineError("Engine returned no id for the new bookmark")
        await self._save()
        return {"bookmark_id": new_id, "title": title, "saved": True}

    async def delete_bookmark(self, bookmark_id: str) -> bool:
        result = await self._send("DestroyBookmark", self._doc_handle, [bookmark_id])
        ok = bool((result or {}).get("qSuccess", False))
        if ok:
            await self._save()
        return ok

    # ── Charts (existing objects) ─────────────────────────────────

    async def get_object_info(self, object_id: str) -> dict:
        """Describe a visualization: type, titles, dimensions, and measures."""
        handle = await self._get_object_handle(object_id)
        props = await self._properties(handle)
        layout = await self._layout(handle)

        hc_def = props.get("qHyperCubeDef") or {}
        hc_layout = layout.get("qHyperCube") or {}
        dim_info = hc_layout.get("qDimensionInfo") or []
        measure_info = hc_layout.get("qMeasureInfo") or []

        dimensions = []
        for i, dim in enumerate(hc_def.get("qDimensions") or []):
            qdef = dim.get("qDef") or {}
            field_defs = qdef.get("qFieldDefs") or []
            labels = qdef.get("qFieldLabels") or []
            fallback = dim_info[i].get("qFallbackTitle") if i < len(dim_info) else None
            dimensions.append({
                "field": field_defs[0] if field_defs else None,
                "label": fallback or (labels[0] if labels else None) or (field_defs[0] if field_defs else None),
                "library_id": dim.get("qLibraryId"),
            })

        measures = []
        for i, measure in enumerate(hc_def.get("qMeasures") or []):
            qdef = measure.get("qDef") or {}
            fallback = measure_info[i].get("qFallbackTitle") if i < len(measure_info) else None
            measures.append({
                "expression": qdef.get("qDef") or None,
                "label": fallback or qdef.get("qLabel") or qdef.get("qDef") or None,
                "library_id": measure.get("qLibraryId"),
            })

        list_def = props.get("qListObjectDef") or {}
        if list_def:
            field_defs = (list_def.get("qDef") or {}).get("qFieldDefs") or []
            lo_title = ((layout.get("qListObject") or {}).get("qDimensionInfo") or {}).get("qFallbackTitle")
            dimensions.append({
                "field": field_defs[0] if field_defs else None,
                "label": lo_title or (field_defs[0] if field_defs else None),
                "library_id": list_def.get("qLibraryId"),
            })

        if hc_def:
            data_source = "hypercube"
        elif list_def:
            data_source = "listobject"
        else:
            data_source = None

        def _text(value: Any) -> str:
            return value if isinstance(value, str) else ""

        return {
            "id": object_id,
            "type": (props.get("qInfo") or {}).get("qType") or props.get("visualization") or "",
            "visualization": props.get("visualization") or "",
            "title": _text(props.get("title")) or _text(layout.get("title")),
            "subtitle": _text(props.get("subtitle")),
            "footnote": _text(props.get("footnote")),
            "data_source": data_source,
            "dimensions": dimensions,
            "measures": measures,
            "total_rows": (hc_layout.get("qSize") or {}).get("qcy"),
        }

    async def get_object_data(
        self, object_id: str, max_rows: int = 1000, page_size: int = 1000
    ) -> HypercubeResult:
        """Read the computed data behind an existing chart or list box."""
        handle = await self._get_object_handle(object_id)
        layout = await self._layout(handle)

        if layout.get("qHyperCube"):
            hc = layout["qHyperCube"]
            if hc.get("qMode") in ("P", "K", "T"):
                raise EngineError(
                    "Pivot and stacked charts are not readable directly; call "
                    "qlik_create_data_object with the chart's dimensions and measures instead"
                )
            return await self._read_hypercube(hc, handle, max_rows=max_rows, page_size=page_size)

        if layout.get("qListObject"):
            lo = layout["qListObject"]
            title = (lo.get("qDimensionInfo") or {}).get("qFallbackTitle") or "Value"
            rows = _rows_from_pages(lo.get("qDataPages") or [])
            total = (lo.get("qSize") or {}).get("qcy", len(rows))
            while len(rows) < min(total, max_rows):
                raw = await self._send("GetListObjectData", handle, [
                    "/qListObjectDef",
                    [{"qTop": len(rows), "qLeft": 0, "qHeight": min(page_size, max_rows - len(rows)), "qWidth": 1}],
                ])
                more = _rows_from_pages(_unwrap(raw, "qDataPages") or [])
                if not more:
                    break
                rows.extend(more)
            rows = rows[:max_rows]
            return HypercubeResult(headers=[title], rows=rows, total_rows=total, truncated=len(rows) < total)

        raise EngineError(f"Object {object_id} has no hypercube or list object data")

    # ── Hypercubes ────────────────────────────────────────────────

    async def _read_hypercube(
        self, hc: dict, handle: int, max_rows: int, page_size: int, path: str = "/qHyperCubeDef"
    ) -> HypercubeResult:
        """Turn a hypercube layout into rows, paging past the initial fetch as needed."""
        dim_info = hc.get("qDimensionInfo") or []
        measure_info = hc.get("qMeasureInfo") or []

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

        rows = _rows_from_pages(hc.get("qDataPages") or [])
        total_rows = (hc.get("qSize") or {}).get("qcy", len(rows))
        width = max(1, len(headers))
        page_rows = max(1, min(page_size, _MAX_PAGE_CELLS // width))

        while len(rows) < min(total_rows, max_rows):
            raw = await self._send("GetHyperCubeData", handle, [
                path,
                [{
                    "qTop": len(rows),
                    "qLeft": 0,
                    "qHeight": min(page_rows, max_rows - len(rows)),
                    "qWidth": width,
                }],
            ])
            new_rows = _rows_from_pages(_unwrap(raw, "qDataPages") or [])
            if not new_rows:
                break
            rows.extend(new_rows)

        rows = rows[:max_rows]
        return HypercubeResult(
            headers=headers,
            rows=rows,
            total_rows=total_rows,
            truncated=len(rows) < total_rows,
        )

    async def create_hypercube(
        self,
        dimensions: list[str],
        measures: list[str],
        page_size: int = 1000,
        max_rows: int = 10000,
        filters: Optional[list[dict]] = None,
        sort_by: Optional[str] = None,
        sort_descending: bool = False,
    ) -> HypercubeResult:
        """Create a session hypercube and fetch its data.

        Args:
            dimensions: Field names or expressions for dimensions.
            measures: Expressions for measures (e.g., "Sum(Revenue)").
            page_size: Rows per page for data fetching.
            max_rows: Maximum total rows to retrieve.
            filters: ``[{"field": ..., "values": [...]}, ...]`` applied in a
                temporary alternate state, so they never touch the session's
                own selections. Unmatched filters are reported on the result.
            sort_by: Column (dimension or measure label) to sort by; default is
                load order for dimensions.
            sort_descending: Sort direction when ``sort_by`` is set.
        """
        state: Optional[str] = None
        unmatched: list[dict] = []
        try:
            if filters:
                state = await self._add_alternate_state()
                for f in filters:
                    values = list(f.get("values") or [])
                    ok = await self.apply_selections(f["field"], values, state=state)
                    if not ok:
                        unmatched.append({"field": f["field"], "values": values})

            sort_dir = -1 if sort_descending else 1
            q_dimensions = []
            for dim in dimensions:
                criteria = {"qSortByLoadOrder": 1}
                if sort_by and sort_by == dim:
                    criteria = {"qSortByAscii": sort_dir, "qSortByNumeric": sort_dir, "qSortByLoadOrder": 0}
                q_dimensions.append({
                    "qDef": {"qFieldDefs": [dim], "qSortCriterias": [criteria]},
                })
            q_measures = []
            for measure in measures:
                entry: dict = {"qDef": {"qDef": measure, "qLabel": measure}}
                if sort_by and sort_by == measure:
                    entry["qSortBy"] = {"qSortByNumeric": sort_dir}
                q_measures.append(entry)

            columns = list(dimensions) + list(measures)
            inter_column_sort = list(range(len(columns)))
            if sort_by and sort_by in columns:
                idx = columns.index(sort_by)
                inter_column_sort = [idx] + [i for i in inter_column_sort if i != idx]

            width = max(1, len(columns))
            initial_fetch = max(1, min(page_size, max_rows, _MAX_PAGE_CELLS // width))

            hc_def: dict = {
                "qDimensions": q_dimensions,
                "qMeasures": q_measures,
                "qInterColumnSortOrder": inter_column_sort,
                "qInitialDataFetch": [
                    {"qTop": 0, "qLeft": 0, "qHeight": initial_fetch, "qWidth": width}
                ],
            }
            if state:
                hc_def["qStateName"] = state

            handle = await self._create_session_object({
                "qInfo": {"qType": "hypercube"},
                "qHyperCubeDef": hc_def,
            })

            layout = await self._layout(handle)
            if not layout:
                raise EngineError("Empty layout from hypercube")

            result = await self._read_hypercube(
                layout.get("qHyperCube") or {}, handle, max_rows=max_rows, page_size=page_size,
            )
            result.unmatched_filters = unmatched
            return result
        finally:
            if state and not self.broken:
                await self._remove_alternate_state(state)

    # ── Sheet creation and editing ────────────────────────────────

    async def _create_children(
        self, parent_handle: int, objects: list[dict]
    ) -> tuple[list[dict], list[str]]:
        created: list[dict] = []
        failed: list[str] = []
        for i, obj_def in enumerate(objects):
            try:
                child_props = self._build_child_props(obj_def)
                child = await self._send("CreateChild", parent_handle, [child_props])
                child_id = _generic_id(child)
                if not child_id:
                    raise EngineError("Engine returned no id for child object")
                created.append({
                    "id": child_id,
                    "type": child_props["qInfo"]["qType"],
                    "title": child_props.get("title", ""),
                })
            except EngineError as e:
                logger.warning("Failed to create child object %d: %s", i, e)
                failed.append(f"{obj_def.get('title') or obj_def.get('type', '?')}: {e}")
        return created, failed

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

        sheet_handle = _generic_handle(result)
        sheet_id = _generic_id(result)

        created: list[dict] = []
        failed: list[str] = []
        if objects and sheet_handle is not None:
            created, failed = await self._create_children(sheet_handle, objects)

        if created and sheet_handle is not None:
            props = await self._properties(sheet_handle) or sheet_props
            props["cells"] = self._layout_cells(created)
            await self._send("SetProperties", sheet_handle, [props])

        await self._save()

        return {
            "sheet_id": sheet_id,
            "title": title,
            "object_count": len(created),
            "objects": created,
            "failed_objects": failed,
            "saved": True,
        }

    async def _place_on_sheet(self, sheet_handle: int, created: list[dict]) -> None:
        """Append cells for newly created children to an existing sheet and persist."""
        props = await self._properties(sheet_handle)
        existing = list(props.get("cells") or [])
        new_cells = self._append_cells(existing, created)
        props["cells"] = existing + new_cells

        bottom = max((c.get("row", 0) + c.get("rowspan", 1) for c in props["cells"]), default=0)
        rows = props.get("rows") or _SHEET_ROWS
        if bottom > rows:
            # Qlik Cloud extended sheets grow vertically beyond the 12-row grid.
            props["rows"] = bottom
            layout_options = props.get("layoutOptions") or {}
            layout_options["extendable"] = True
            props["layoutOptions"] = layout_options
        props.setdefault("columns", _SHEET_COLUMNS)
        await self._send("SetProperties", sheet_handle, [props])

    async def add_objects_to_sheet(self, sheet_id: str, objects: list[dict]) -> dict:
        """Add visualizations to an existing sheet, place them, and save."""
        sheet_handle = await self._get_object_handle(sheet_id)
        created, failed = await self._create_children(sheet_handle, objects)
        if created:
            await self._place_on_sheet(sheet_handle, created)
        await self._save()
        return {
            "sheet_id": sheet_id,
            "object_count": len(created),
            "objects": created,
            "failed_objects": failed,
            "saved": True,
        }

    async def add_filter_pane(self, sheet_id: str, fields: list[str], title: str = "Filters") -> dict:
        """Add a filter pane with one list box per field to an existing sheet, and save."""
        sheet_handle = await self._get_object_handle(sheet_id)

        pane_props = {
            "qInfo": {"qType": "filterpane"},
            "visualization": "filterpane",
            "title": title or "Filters",
            "showTitles": True,
            "qChildListDef": {"qData": {"title": "/title", "visualization": "/visualization"}},
        }
        pane = await self._send("CreateChild", sheet_handle, [pane_props])
        pane_handle = _generic_handle(pane)
        pane_id = _generic_id(pane)
        if pane_handle is None or not pane_id:
            raise EngineError("Failed to create filter pane")

        listbox_ids = []
        for field_name in fields:
            listbox_props = {
                "qInfo": {"qType": "listbox"},
                "visualization": "listbox",
                "title": field_name,
                "qListObjectDef": {
                    "qDef": {
                        "qFieldDefs": [field_name],
                        "qSortCriterias": [{"qSortByState": 1, "qSortByAscii": 1}],
                    },
                    "qShowAlternatives": True,
                    "qFrequencyMode": "N",
                    "qInitialDataFetch": [{"qTop": 0, "qLeft": 0, "qHeight": 100, "qWidth": 1}],
                },
            }
            child = await self._send("CreateChild", pane_handle, [listbox_props])
            listbox_ids.append(_generic_id(child))

        await self._place_on_sheet(sheet_handle, [{"id": pane_id, "type": "filterpane", "title": title}])
        await self._save()

        return {
            "sheet_id": sheet_id,
            "filter_pane_id": pane_id,
            "fields": list(fields),
            "listbox_ids": listbox_ids,
            "saved": True,
        }

    async def close(self) -> None:
        """Close the WebSocket connection."""
        try:
            await self._ws.close()
        except Exception as e:  # noqa: BLE001 - closing is best effort
            logger.debug("Ignoring error while closing Engine WebSocket: %s", e)

    # ── Static helpers ────────────────────────────────────────────

    @staticmethod
    def _extract_cells(layout: dict) -> list[dict]:
        """Extract visualization cells (grid placement) from a sheet layout."""
        cells = []
        for cell in layout.get("cells") or []:
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
    def _cell(obj_id: str, obj_type: str, col: int, row: int, colspan: int, rowspan: int) -> dict:
        return {
            "name": obj_id,
            "type": obj_type,
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
        }

    @classmethod
    def _layout_cells(cls, created: list[dict]) -> list[dict]:
        """Arrange created objects on a fresh 24 x 12 sheet grid."""
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

        return [
            cls._cell(
                obj["id"], obj["type"],
                (i % per_row) * colspan, (i // per_row) * rowspan, colspan, rowspan,
            )
            for i, obj in enumerate(created)
        ]

    @classmethod
    def _append_cells(cls, existing: list[dict], created: list[dict]) -> list[dict]:
        """Place new objects in rows below everything already on the sheet."""
        start_row = max((c.get("row", 0) + c.get("rowspan", 1) for c in existing), default=0)
        per_row = _SHEET_COLUMNS // _APPEND_COLSPAN
        return [
            cls._cell(
                obj["id"], obj["type"],
                (i % per_row) * _APPEND_COLSPAN,
                start_row + (i // per_row) * _APPEND_ROWSPAN,
                _APPEND_COLSPAN, _APPEND_ROWSPAN,
            )
            for i, obj in enumerate(created)
        ]

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


@dataclass
class _PooledSession:
    session: Optional[EngineSession]
    lock: asyncio.Lock
    last_used: float


class EngineClient:
    """Factory and pool for Engine API sessions.

    With ``qlik.reuse_sessions`` (default) one socket per app is kept open
    for ``qlik.session_idle_seconds`` after its last use, which removes the
    connect and OpenDoc round trips from every subsequent call. Calls to the
    same app are serialized on that socket; different apps run in parallel.
    """

    def __init__(self, config: Config, auth: AuthManager) -> None:
        self.config = config
        self.auth = auth
        self._pool: dict[str, _PooledSession] = {}
        self._pool_lock = asyncio.Lock()

    async def _connect(self, app_id: str) -> EngineSession:
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
            doc_handle = _generic_handle(result)
            if doc_handle is None:
                raise EngineError(f"No document handle returned for: {app_id}")
            session._doc_handle = doc_handle
        except BaseException:
            await session.close()
            raise
        logger.debug("Engine session opened for app %s (handle=%d)", app_id, session._doc_handle)
        return session

    @asynccontextmanager
    async def open_app(self, app_id: str) -> AsyncIterator[EngineSession]:
        """Open (or reuse) a WebSocket session to a Qlik app.

        Usage:
            async with engine_client.open_app("app-id") as session:
                sheets = await session.get_sheets()
        """
        _validate_id(app_id, "app_id")

        if not self.config.qlik.reuse_sessions:
            session = await self._connect(app_id)
            try:
                yield session
            finally:
                await session.close()
                logger.debug("Engine session closed for app %s", app_id)
            return

        entry = await self._acquire(app_id)
        try:
            if entry.session is None:
                entry.session = await self._connect(app_id)
            yield entry.session
        except BaseException:
            if entry.session is not None and entry.session.broken:
                await self._evict(app_id, entry)
            raise
        finally:
            if entry.session is not None:
                if entry.session.broken:
                    await self._evict(app_id, entry)
                else:
                    try:
                        await entry.session.cleanup_temp()
                    except EngineError:
                        await self._evict(app_id, entry)
            elif app_id in self._pool and self._pool[app_id] is entry:
                del self._pool[app_id]
            entry.last_used = time.monotonic()
            entry.lock.release()
            await self._evict_idle()

    async def _acquire(self, app_id: str) -> _PooledSession:
        async with self._pool_lock:
            entry = self._pool.get(app_id)
            if entry is None:
                entry = _PooledSession(session=None, lock=asyncio.Lock(), last_used=time.monotonic())
                self._pool[app_id] = entry
        await entry.lock.acquire()
        # Re-check under the lock: the entry may have expired while we waited.
        if entry.session is not None and self._expired(entry):
            await entry.session.close()
            entry.session = None
        if self._pool.get(app_id) is not entry:
            # Evicted while waiting; register again.
            async with self._pool_lock:
                self._pool[app_id] = entry
        return entry

    def _expired(self, entry: _PooledSession) -> bool:
        return time.monotonic() - entry.last_used > self.config.qlik.session_idle_seconds

    async def _evict(self, app_id: str, entry: _PooledSession) -> None:
        if entry.session is not None:
            await entry.session.close()
            entry.session = None
        if self._pool.get(app_id) is entry:
            del self._pool[app_id]

    async def _evict_idle(self) -> None:
        """Close idle sessions and enforce the pool size limit."""
        async with self._pool_lock:
            victims = [
                (app_id, entry) for app_id, entry in self._pool.items()
                if not entry.lock.locked() and (entry.session is None or self._expired(entry))
            ]
            for app_id, _ in victims:
                del self._pool[app_id]
            overflow = len(self._pool) - self.config.qlik.max_sessions
            if overflow > 0:
                idle = sorted(
                    ((app_id, entry) for app_id, entry in self._pool.items() if not entry.lock.locked()),
                    key=lambda item: item[1].last_used,
                )
                for app_id, entry in idle[:overflow]:
                    victims.append((app_id, entry))
                    del self._pool[app_id]
        for _, entry in victims:
            if entry.session is not None:
                await entry.session.close()

    async def close(self) -> None:
        """Close every pooled session."""
        async with self._pool_lock:
            entries = list(self._pool.values())
            self._pool.clear()
        for entry in entries:
            if entry.session is not None:
                await entry.session.close()
