"""qlik_get_chart_info and qlik_get_chart_data: read existing visualizations."""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineError
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)


class GetChartInfoInput(BaseModel):
    """Input schema for qlik_get_chart_info."""

    app_id: str = Field(description="The Qlik Cloud app ID")
    object_id: str = Field(
        description="Id of the chart or list box (from qlik_get_sheet_details objects[].id)",
        min_length=1, max_length=256,
    )


class GetChartDataInput(BaseModel):
    """Input schema for qlik_get_chart_data."""

    app_id: str = Field(description="The Qlik Cloud app ID")
    object_id: str = Field(
        description="Id of the chart or list box (from qlik_get_sheet_details objects[].id)",
        min_length=1, max_length=256,
    )
    max_rows: Optional[int] = Field(
        default=1000, ge=1,
        description="Maximum number of rows to return (default 1000; capped by server config)",
    )


GET_CHART_INFO_DESCRIPTION = (
    "Describe an existing chart in a Qlik Cloud app: its type, title, and the dimensions "
    "and measure expressions it is built from (library/master item ids when used). Use this to "
    "understand how a dashboard metric is defined before reproducing or extending it."
)

GET_CHART_DATA_DESCRIPTION = (
    "Read the computed data behind an existing chart or list box in a Qlik Cloud app as a table. "
    "Use this to answer questions from a dashboard exactly as it is shown. For pivot or stacked "
    "charts, use qlik_get_hypercube_data with the chart's dimensions and measures instead."
)


async def handle_get_chart_info(ctx: ToolContext, params: dict) -> dict:
    """Execute the qlik_get_chart_info tool."""
    input_data = GetChartInfoInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            info = await session.get_object_info(input_data.object_id)
            return {"app_id": input_data.app_id, **info}
    except EngineError as e:
        logger.error("Engine error in get_chart_info: %s", e)
        return {"error": str(e), "app_id": input_data.app_id, "object_id": input_data.object_id,
                "hint": "Find valid object ids with qlik_get_sheet_details."}


async def handle_get_chart_data(ctx: ToolContext, params: dict, max_rows_limit: int = 10000) -> dict:
    """Execute the qlik_get_chart_data tool."""
    input_data = GetChartDataInput(**params)
    effective_max = min(input_data.max_rows or 1000, max_rows_limit)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            result = await session.get_object_data(input_data.object_id, max_rows=effective_max)
            return {"app_id": input_data.app_id, "object_id": input_data.object_id, **result.as_payload()}
    except EngineError as e:
        logger.error("Engine error in get_chart_data: %s", e)
        return {"error": str(e), "app_id": input_data.app_id, "object_id": input_data.object_id,
                "hint": "Find valid object ids with qlik_get_sheet_details."}


GET_CHART_INFO_SPEC = ToolSpec(
    name="qlik_get_chart_info",
    title="Get chart info",
    description=GET_CHART_INFO_DESCRIPTION,
    input_model=GetChartInfoInput,
    run=handle_get_chart_info,
)

GET_CHART_DATA_SPEC = ToolSpec(
    name="qlik_get_chart_data",
    title="Get chart data",
    description=GET_CHART_DATA_DESCRIPTION,
    input_model=GetChartDataInput,
    run=lambda ctx, params: handle_get_chart_data(
        ctx, params, max_rows_limit=ctx.config.tools.max_hypercube_rows,
    ),
)
