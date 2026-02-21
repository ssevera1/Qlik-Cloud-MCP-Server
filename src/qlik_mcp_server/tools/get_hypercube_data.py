"""qlik_get_hypercube_data — Primary governed data retrieval tool.

This is the main data access tool. The agent can request specific slices
of data (hypercubes) from the Qlik engine with dimensions and measures.
All data is fully governed by Qlik's Section Access security rules.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineClient, EngineError

logger = logging.getLogger(__name__)


class Filter(BaseModel):
    """A field selection/filter to apply before data retrieval."""

    field: str = Field(description="The field name to filter on")
    values: list[str] = Field(description="Values to select in the field")


class GetHypercubeDataInput(BaseModel):
    """Input schema for qlik_get_hypercube_data."""

    app_id: str = Field(
        description="The Qlik Cloud app ID to retrieve data from"
    )
    dimensions: list[str] = Field(
        description=(
            "List of field names or expressions for the dimensions (grouping columns). "
            "Example: ['Region', 'Product Category']"
        )
    )
    measures: list[str] = Field(
        description=(
            "List of aggregation expressions for the measures (computed values). "
            "Example: ['Sum(Revenue)', 'Count(OrderID)', 'Avg(UnitPrice)']"
        )
    )
    filters: Optional[list[Filter]] = Field(
        default=None,
        description=(
            "Optional filters to apply before retrieving data. "
            "Each filter selects specific values in a field. "
            "Example: [{field: 'Year', values: ['2025']}, {field: 'Region', values: ['East', 'West']}]"
        )
    )
    max_rows: Optional[int] = Field(
        default=1000,
        description="Maximum number of rows to return (default: 1000, max: 10000)"
    )


TOOL_DESCRIPTION = (
    "Retrieve aggregated data from a Qlik Cloud app as a table with dimensions and measures. "
    "Dimensions define the grouping (e.g., Region, Product) and measures define the calculations "
    "(e.g., Sum(Revenue), Count(Orders)). Data is governed by Qlik Section Access security — "
    "only data the service account is authorized to see will be returned. "
    "Use filters to narrow the data before retrieval."
)


async def handle_get_hypercube_data(
    engine: EngineClient, params: dict, max_rows_limit: int = 10000
) -> dict:
    """Execute the qlik_get_hypercube_data tool."""
    input_data = GetHypercubeDataInput(**params)

    # Enforce row limit
    effective_max = min(input_data.max_rows or 1000, max_rows_limit)

    try:
        async with engine.open_app(input_data.app_id) as session:
            # Apply filters if provided
            if input_data.filters:
                for f in input_data.filters:
                    await session.apply_selections(f.field, f.values)
                    logger.debug("Applied filter: %s = %s", f.field, f.values)

            # Create and fetch hypercube
            result = await session.create_hypercube(
                dimensions=input_data.dimensions,
                measures=input_data.measures,
                max_rows=effective_max,
            )

            return {
                "app_id": input_data.app_id,
                "headers": result.headers,
                "data": result.rows,
                "row_count": len(result.rows),
                "total_rows": result.total_rows,
                "truncated": result.truncated,
                "filters_applied": [
                    {"field": f.field, "values": f.values}
                    for f in (input_data.filters or [])
                ],
                "table": result.to_table(),
            }

    except EngineError as e:
        logger.error("Engine error in get_hypercube_data: %s", e)
        return {
            "error": str(e),
            "app_id": input_data.app_id,
            "hint": (
                "Check that dimension field names exist in the app's data model "
                "and measure expressions use valid Qlik syntax (e.g., Sum(FieldName))."
            ),
        }
