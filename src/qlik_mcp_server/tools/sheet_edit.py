"""qlik_add_chart and qlik_add_filter: extend an existing sheet."""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineError
from .create_sheet import _ALLOWED_VIS_TYPES
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)


class AddChartInput(BaseModel):
    """Input schema for qlik_add_chart."""

    app_id: str = Field(description="The Qlik Cloud app ID")
    sheet_id: str = Field(description="Id of the sheet to add the chart to (see qlik_list_sheets)",
                          min_length=1, max_length=256)
    type: str = Field(
        description=(
            "Visualization type: 'barchart', 'linechart', 'piechart', 'table', 'kpi', 'scatterplot', "
            "'treemap', 'combochart', 'gauge', 'waterfallchart', 'boxplot', 'distributionplot', "
            "'histogram', 'pivot-table', 'mekkochart', 'bulletchart'"
        )
    )
    title: str = Field(default="", description="Title for the chart", max_length=512)
    dimensions: list[str] = Field(
        default_factory=list, max_length=20,
        description="Field names for dimensions (e.g., ['Region']); empty for a KPI",
    )
    measures: list[str] = Field(
        default_factory=list, max_length=30,
        description="Aggregation expressions (e.g., ['Sum(Revenue)'])",
    )


class AddFilterInput(BaseModel):
    """Input schema for qlik_add_filter."""

    app_id: str = Field(description="The Qlik Cloud app ID")
    sheet_id: str = Field(description="Id of the sheet to add the filter pane to", min_length=1, max_length=256)
    fields: list[str] = Field(
        description="Field names to offer as filters, one list box each (e.g., ['Region', 'Year'])",
        min_length=1, max_length=20,
    )
    title: Optional[str] = Field(default="Filters", max_length=512, description="Title of the filter pane")


ADD_CHART_DESCRIPTION = (
    "Add one visualization to an existing sheet in a Qlik Cloud app and save the app. The chart is "
    "placed below the sheet's current content. Use qlik_create_sheet to start a new sheet instead."
)

ADD_FILTER_DESCRIPTION = (
    "Add a filter pane with one list box per field to an existing sheet in a Qlik Cloud app and "
    "save the app, so users can interactively filter the sheet's charts."
)


def _sheet_url(ctx: ToolContext, app_id: str, sheet_id: str) -> str:
    return f"https://{ctx.config.tenant_host}/sense/app/{app_id}/sheet/{sheet_id}/state/analysis"


async def handle_add_chart(ctx: ToolContext, params: dict) -> dict:
    """Execute the qlik_add_chart tool."""
    input_data = AddChartInput(**params)
    if input_data.type not in _ALLOWED_VIS_TYPES:
        return {
            "error": f"Invalid visualization type: '{input_data.type}'",
            "allowed_types": sorted(_ALLOWED_VIS_TYPES),
            "app_id": input_data.app_id,
        }
    if not input_data.measures and input_data.type != "table":
        return {"error": "At least one measure is required for this chart type", "app_id": input_data.app_id}

    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            result = await session.add_objects_to_sheet(input_data.sheet_id, [{
                "type": input_data.type,
                "title": input_data.title,
                "dimensions": input_data.dimensions,
                "measures": input_data.measures,
            }])
            if not result["objects"]:
                return {"error": "The engine rejected the chart definition", "app_id": input_data.app_id,
                        "details": result.get("failed_objects", []),
                        "hint": "Check field names with qlik_get_fields and expression syntax."}
            return {
                "app_id": input_data.app_id,
                "sheet_id": input_data.sheet_id,
                "object_id": result["objects"][0]["id"],
                "type": input_data.type,
                "title": input_data.title,
                "saved": result["saved"],
                "url": _sheet_url(ctx, input_data.app_id, input_data.sheet_id),
            }
    except EngineError as e:
        logger.error("Engine error in add_chart: %s", e)
        return {"error": str(e), "app_id": input_data.app_id, "sheet_id": input_data.sheet_id,
                "hint": "Verify the sheet_id (qlik_list_sheets) and that the service account can edit the app."}


async def handle_add_filter(ctx: ToolContext, params: dict) -> dict:
    """Execute the qlik_add_filter tool."""
    input_data = AddFilterInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            result = await session.add_filter_pane(
                input_data.sheet_id, input_data.fields, title=input_data.title or "Filters",
            )
            return {
                "app_id": input_data.app_id,
                **result,
                "url": _sheet_url(ctx, input_data.app_id, input_data.sheet_id),
            }
    except EngineError as e:
        logger.error("Engine error in add_filter: %s", e)
        return {"error": str(e), "app_id": input_data.app_id, "sheet_id": input_data.sheet_id,
                "hint": "Verify the sheet_id (qlik_list_sheets) and the field names (qlik_get_fields)."}


ADD_CHART_SPEC = ToolSpec(
    name="qlik_add_chart",
    title="Add chart to sheet",
    description=ADD_CHART_DESCRIPTION,
    input_model=AddChartInput,
    run=handle_add_chart,
    writes=True,
)

ADD_FILTER_SPEC = ToolSpec(
    name="qlik_add_filter",
    title="Add filter pane to sheet",
    description=ADD_FILTER_DESCRIPTION,
    input_model=AddFilterInput,
    run=handle_add_filter,
    writes=True,
)
