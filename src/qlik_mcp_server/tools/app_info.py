"""qlik_describe_app and qlik_list_sheets: orientation before deeper analysis."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..engine_client import EngineError
from ..qlik_cloud_client import QlikCloudError
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)


class DescribeAppInput(BaseModel):
    """Input schema for qlik_describe_app."""

    app_id: str = Field(description="The Qlik Cloud app ID to describe")


class ListSheetsInput(BaseModel):
    """Input schema for qlik_list_sheets."""

    app_id: str = Field(description="The Qlik Cloud app ID whose sheets to list")


DESCRIBE_APP_DESCRIPTION = (
    "Get an overview of a Qlik Cloud app: name, description, owner, space, last reload time, "
    "whether Section Access applies, the data model tables, and counts of sheets, fields, "
    "master dimensions, master measures, and bookmarks. Call this first when you are pointed "
    "at an unfamiliar app_id."
)

LIST_SHEETS_DESCRIPTION = (
    "List the sheets (dashboard pages) in a Qlik Cloud app with their ids, titles, "
    "descriptions, and published state. Cheaper than qlik_get_sheet_details; use it to pick "
    "a sheet_id, then inspect that sheet's charts with qlik_get_sheet_details."
)


async def handle_describe_app(ctx: ToolContext, params: dict) -> dict:
    """Execute the qlik_describe_app tool."""
    input_data = DescribeAppInput(**params)
    app_id = input_data.app_id
    tenant_host = ctx.config.tenant_host

    try:
        attrs = await ctx.qlik_client.get_app(app_id)
    except QlikCloudError as e:
        logger.error("REST error in describe_app: %s", e)
        return {"error": str(e), "app_id": app_id,
                "hint": "Verify the app_id (use qlik_search) and that the service account can read the app."}

    tables: list[dict] = []
    field_count = None
    try:
        metadata = await ctx.qlik_client.get_app_data_metadata(app_id)
        for table in (metadata or {}).get("tables") or []:
            tables.append({
                "name": table.get("name", ""),
                "rows": table.get("no_of_rows", 0),
                "fields": table.get("no_of_fields", len(table.get("fields") or [])),
            })
        fields = (metadata or {}).get("fields")
        if isinstance(fields, list):
            field_count = len(fields)
    except QlikCloudError as e:
        # Data metadata is unavailable for some app types (Direct Query, unreloaded apps).
        logger.info("App data metadata unavailable for %s: %s", app_id, e)

    counts: dict = {}
    warning = None
    try:
        async with ctx.engine.open_app(app_id) as session:
            sheets = await session.list_sheets()
            master = await session.get_master_items()
            bookmarks = await session.get_bookmarks()
            if field_count is None:
                field_count = len(await session.get_fields())
            counts = {
                "sheet_count": len(sheets),
                "sheets": sheets,
                "master_dimension_count": len(master["dimensions"]),
                "master_measure_count": len(master["measures"]),
                "bookmark_count": len(bookmarks),
            }
    except EngineError as e:
        logger.warning("Engine unavailable while describing app %s: %s", app_id, e)
        warning = f"Engine details unavailable: {e}"

    payload = {
        "app_id": app_id,
        "name": attrs.get("name", ""),
        "description": attrs.get("description", ""),
        "owner_id": attrs.get("ownerId", ""),
        "space_id": attrs.get("spaceId", ""),
        "created": attrs.get("createdDate", ""),
        "modified": attrs.get("modifiedDate", ""),
        "last_reload_time": attrs.get("lastReloadTime", ""),
        "published": bool(attrs.get("published", False)),
        "has_section_access": bool(attrs.get("hasSectionAccess", False)),
        "usage": attrs.get("usage", ""),
        "url": f"https://{tenant_host}/sense/app/{app_id}",
        "tables": tables,
        "field_count": field_count,
        **counts,
    }
    if warning:
        payload["warning"] = warning
    return payload


async def handle_list_sheets(ctx: ToolContext, params: dict) -> dict:
    """Execute the qlik_list_sheets tool."""
    input_data = ListSheetsInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            sheets = await session.list_sheets()
            return {"app_id": input_data.app_id, "sheet_count": len(sheets), "sheets": sheets}
    except EngineError as e:
        logger.error("Engine error in list_sheets: %s", e)
        return {"error": str(e), "app_id": input_data.app_id,
                "hint": "Verify the app_id exists and the service account has access."}


DESCRIBE_APP_SPEC = ToolSpec(
    name="qlik_describe_app",
    title="Describe app",
    description=DESCRIBE_APP_DESCRIPTION,
    input_model=DescribeAppInput,
    run=handle_describe_app,
)

LIST_SHEETS_SPEC = ToolSpec(
    name="qlik_list_sheets",
    title="List sheets",
    description=LIST_SHEETS_DESCRIPTION,
    input_model=ListSheetsInput,
    run=handle_list_sheets,
)
