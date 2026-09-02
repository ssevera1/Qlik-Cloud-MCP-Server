"""qlik_create_sheet: build analysis views on demand.

If a user asks a novel question that no existing dashboard answers, the
agent can build a new sheet with charts, which is then saved into the app.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineClient, EngineError

logger = logging.getLogger(__name__)


# Visualization types whose properties are driven by a single qHyperCubeDef.
# Types with other data structures (filterpane, text-image, map) are excluded
# because the simple dimensions/measures definition would not render them.
_ALLOWED_VIS_TYPES = frozenset({
    "barchart", "linechart", "piechart", "table", "kpi",
    "scatterplot", "treemap", "combochart", "gauge", "waterfallchart",
    "boxplot", "distributionplot", "histogram", "pivot-table", "mekkochart",
    "bulletchart",
})


class VisualizationObject(BaseModel):
    """Definition for a visualization to add to the sheet."""

    type: str = Field(
        description=(
            "Visualization type: 'barchart', 'linechart', 'piechart', 'table', "
            "'kpi', 'scatterplot', 'treemap', 'combochart', 'gauge', "
            "'waterfallchart', 'boxplot', 'distributionplot', 'histogram', "
            "'pivot-table', 'mekkochart', 'bulletchart'"
        )
    )
    title: str = Field(default="", description="Title for the visualization", max_length=512)
    dimensions: list[str] = Field(
        default_factory=list,
        description="Field names for dimensions (e.g., ['Region', 'Product'])",
        max_length=20,
    )
    measures: list[str] = Field(
        default_factory=list,
        description="Aggregation expressions (e.g., ['Sum(Revenue)', 'Count(OrderID)'])",
        max_length=30,
    )


class CreateSheetInput(BaseModel):
    """Input schema for qlik_create_sheet."""

    app_id: str = Field(description="The Qlik Cloud app ID to create the sheet in")
    title: str = Field(description="Title for the new sheet", min_length=1, max_length=256)
    description: Optional[str] = Field(default="", description="Optional description for the sheet")
    objects: list[VisualizationObject] = Field(
        default_factory=list,
        description=(
            "List of visualization objects to add to the sheet (up to 24). "
            "Each object defines a chart type, dimensions, and measures."
        ),
        max_length=24,
    )


TOOL_DESCRIPTION = (
    "Create a new analysis sheet in a Qlik Cloud app with visualizations, and save the app. "
    "Use this when no existing dashboard answers the user's question. "
    "You can add multiple visualization objects (bar charts, line charts, KPIs, tables) "
    "with specified dimensions and measures; they are laid out automatically on the sheet. "
    "The sheet title is prefixed (default '[Agent]') to distinguish it from manually "
    "created sheets. Returns the sheet ID and a link to view it."
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

    for obj in input_data.objects:
        if obj.type not in _ALLOWED_VIS_TYPES:
            return {
                "error": f"Invalid visualization type: '{obj.type}'",
                "allowed_types": sorted(_ALLOWED_VIS_TYPES),
                "app_id": input_data.app_id,
            }

    prefixed_title = f"{sheet_prefix}{input_data.title}"

    try:
        async with engine.open_app(input_data.app_id) as session:
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
                f"/sheet/{result['sheet_id']}/state/analysis"
            )

            return {
                "app_id": input_data.app_id,
                "sheet_id": result["sheet_id"],
                "title": prefixed_title,
                "object_count": result["object_count"],
                "objects": result.get("objects", []),
                "failed_objects": result.get("failed_objects", []),
                "saved": result.get("saved", False),
                "url": sheet_url,
                "note": (
                    "The sheet was saved into the app and will remain until "
                    "someone deletes it. It is private to the account that created it "
                    "until published."
                ),
            }

    except EngineError as e:
        logger.error("Engine error in create_sheet: %s", e)
        return {
            "error": str(e),
            "app_id": input_data.app_id,
            "hint": "Verify the app_id and ensure the service account has edit access to the app.",
        }
