"""qlik_create_sheet — Dynamically build temporary analysis views.

An advanced capability where an agent can dynamically construct a
temporary analysis view. If a user asks a novel question that no
existing dashboard answers, the agent can build a new sheet with
appropriate charts and filters.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineClient, EngineError

logger = logging.getLogger(__name__)


class VisualizationObject(BaseModel):
    """Definition for a visualization to add to the sheet."""

    type: str = Field(
        description=(
            "Visualization type: 'barchart', 'linechart', 'piechart', "
            "'table', 'kpi', 'scatterplot', 'treemap', 'combochart'"
        )
    )
    title: str = Field(
        default="",
        description="Title for the visualization"
    )
    dimensions: list[str] = Field(
        default_factory=list,
        description="Field names for dimensions (e.g., ['Region', 'Product'])"
    )
    measures: list[str] = Field(
        default_factory=list,
        description="Aggregation expressions (e.g., ['Sum(Revenue)', 'Count(OrderID)'])"
    )


class CreateSheetInput(BaseModel):
    """Input schema for qlik_create_sheet."""

    app_id: str = Field(
        description="The Qlik Cloud app ID to create the sheet in"
    )
    title: str = Field(
        description="Title for the new sheet"
    )
    description: Optional[str] = Field(
        default="",
        description="Optional description for the sheet"
    )
    objects: list[VisualizationObject] = Field(
        default_factory=list,
        description=(
            "List of visualization objects to add to the sheet. "
            "Each object defines a chart type, dimensions, and measures."
        )
    )


TOOL_DESCRIPTION = (
    "Create a new analysis sheet in a Qlik Cloud app with visualizations. "
    "Use this when no existing dashboard answers the user's question. "
    "You can add multiple visualization objects (bar charts, line charts, KPIs, tables) "
    "with specified dimensions and measures. The sheet title will be prefixed with "
    "'[Agent]' to distinguish it from manually created sheets. "
    "Returns the sheet ID and a link to view it."
)


async def handle_create_sheet(
    engine: EngineClient,
    params: dict,
    sheet_prefix: str = "[Agent] ",
    allow_creation: bool = True,
) -> dict:
    """Execute the qlik_create_sheet tool."""
    if not allow_creation:
        return {
            "error": "Sheet creation is disabled in server configuration.",
            "hint": "Set tools.allow_sheet_creation=true in config.yaml to enable.",
        }

    input_data = CreateSheetInput(**params)

    # Prefix the title
    prefixed_title = f"{sheet_prefix}{input_data.title}"

    try:
        async with engine.open_app(input_data.app_id) as session:
            # Build object definitions
            obj_defs = [
                {
                    "type": obj.type,
                    "title": obj.title,
                    "dimensions": obj.dimensions,
                    "measures": obj.measures,
                }
                for obj in input_data.objects
            ]

            result = await session.create_sheet(
                title=prefixed_title,
                description=input_data.description or "",
                objects=obj_defs,
            )

            tenant_host = engine.config.tenant_host
            sheet_url = (
                f"https://{tenant_host}/sense/app/{input_data.app_id}"
                f"/sheet/{result['sheet_id']}"
            )

            return {
                "app_id": input_data.app_id,
                "sheet_id": result["sheet_id"],
                "title": prefixed_title,
                "object_count": result["object_count"],
                "url": sheet_url,
                "note": (
                    "This is a session sheet — it will persist in the app "
                    "until manually removed or the app is reloaded."
                ),
            }

    except EngineError as e:
        logger.error("Engine error in create_sheet: %s", e)
        return {
            "error": str(e),
            "app_id": input_data.app_id,
            "hint": "Verify the app_id and ensure the service account has edit access.",
        }
