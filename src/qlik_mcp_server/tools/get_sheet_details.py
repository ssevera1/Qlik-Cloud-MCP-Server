"""qlik_get_sheet_details: inspect existing dashboard layouts.

This tool lets AI agents "see" the layout of existing Qlik dashboards.
Before answering a user's question, an agent can check whether a
visualization already exists that answers the query, which prevents
hallucination of non-existent metrics.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineClient, EngineError
from .spec import ToolSpec

logger = logging.getLogger(__name__)


class GetSheetDetailsInput(BaseModel):
    """Input schema for qlik_get_sheet_details."""

    app_id: str = Field(description="The Qlik Cloud app ID to inspect")
    sheet_id: Optional[str] = Field(
        default=None,
        description="Specific sheet ID to get details for. If omitted, returns all sheets.",
    )


TOOL_DESCRIPTION = (
    "Get the layout and visualization details of sheets in a Qlik Cloud app. "
    "Use this to inspect what dashboards already exist before creating new analysis. "
    "Returns sheet titles, descriptions, and the id, type, title, and grid position "
    "of each visualization on the sheet. "
    "If no sheet_id is provided, returns a summary of all sheets in the app."
)


async def handle_get_sheet_details(engine: EngineClient, params: dict) -> dict:
    """Execute the qlik_get_sheet_details tool."""
    input_data = GetSheetDetailsInput(**params)

    try:
        async with engine.open_app(input_data.app_id) as session:
            if input_data.sheet_id:
                layout = await session.get_sheet_layout(input_data.sheet_id)
                return {
                    "app_id": input_data.app_id,
                    "sheet_id": input_data.sheet_id,
                    **session.describe_sheet(layout),
                }

            sheets = await session.get_sheets()
            return {
                "app_id": input_data.app_id,
                "sheet_count": len(sheets),
                "sheets": sheets,
            }

    except EngineError as e:
        logger.error("Engine error in get_sheet_details: %s", e)
        return {
            "error": str(e),
            "app_id": input_data.app_id,
            "hint": "Verify the app_id exists and the service account has access.",
        }


GET_SHEET_DETAILS_SPEC = ToolSpec(
    name="qlik_get_sheet_details",
    title="Inspect sheets",
    description=TOOL_DESCRIPTION,
    input_model=GetSheetDetailsInput,
    run=lambda ctx, params: handle_get_sheet_details(ctx.engine, params),
)
