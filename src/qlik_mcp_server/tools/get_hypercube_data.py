"""qlik_get_hypercube_data: primary governed data retrieval tool.

This is the main data access tool. The agent can request specific slices
of data (hypercubes) from the Qlik engine with dimensions and measures.
All data is fully governed by Qlik's Section Access security rules.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineClient, EngineError
from .spec import ToolSpec

logger = logging.getLogger(__name__)


class Filter(BaseModel):
    """A field selection/filter to apply before data retrieval."""

    field: str = Field(description="The field name to filter on", min_length=1, max_length=256)
    values: list[str] = Field(
        description="Values to select in the field",
        min_length=1, max_length=1000,
    )


class GetHypercubeDataInput(BaseModel):
    """Input schema for qlik_get_hypercube_data."""

    app_id: str = Field(
        description="The Qlik Cloud app ID to retrieve data from"
    )
    dimensions: list[str] = Field(
        description=(
            "List of field names or expressions for the dimensions (grouping columns). "
            "Example: ['Region', 'Product Category']"
        ),
        min_length=1, max_length=20,
    )
    measures: list[str] = Field(
        description=(
            "List of aggregation expressions for the measures (computed values). "
            "Example: ['Sum(Revenue)', 'Count(OrderID)', 'Avg(UnitPrice)']"
        ),
        min_length=1, max_length=30,
    )
    filters: Optional[list[Filter]] = Field(
        default=None,
        description=(
            "Optional filters to apply before retrieving data. "
            "Each filter selects specific values in a field. "
            "Example: [{field: 'Year', values: ['2025']}, {field: 'Region', values: ['East', 'West']}]"
        ),
    )
    bookmark_id: Optional[str] = Field(
        default=None,
        max_length=256,
        description=(
            "Optional bookmark id (see qlik_list_bookmarks). The bookmark's selections are applied "
            "before any filters, so the data reflects that saved view."
        ),
    )
    max_rows: Optional[int] = Field(
        default=1000,
        ge=1,
        description="Maximum number of rows to return (default: 1000, max: 10000)"
    )


TOOL_DESCRIPTION = (
    "Retrieve aggregated data from a Qlik Cloud app as a table with dimensions and measures. "
    "Dimensions define the grouping (e.g., Region, Product) and measures define the calculations "
    "(e.g., Sum(Revenue), Count(Orders)); master measure expressions from qlik_list_measures "
    "can be used directly. Data is governed by Qlik Section Access security: only data the "
    "service account is authorized to see is returned. Use filters, or a bookmark_id, to narrow "
    "the data before retrieval."
)


async def handle_get_hypercube_data(
    engine: EngineClient, params: dict,
    max_rows_limit: int = 10000,
    max_columns_limit: int = 50,
) -> dict:
    """Execute the qlik_get_hypercube_data tool."""
    input_data = GetHypercubeDataInput(**params)

    # Enforce row limit
    effective_max = min(input_data.max_rows or 1000, max_rows_limit)

    # Enforce column limit
    total_columns = len(input_data.dimensions) + len(input_data.measures)
    if total_columns > max_columns_limit:
        return {
            "error": (
                f"Too many columns ({total_columns}). "
                f"Maximum allowed: {max_columns_limit}."
            ),
            "app_id": input_data.app_id,
        }

    try:
        async with engine.open_app(input_data.app_id) as session:
            bookmark_applied = None
            if input_data.bookmark_id:
                if not await session.apply_bookmark(input_data.bookmark_id):
                    return {
                        "error": f"Bookmark not found or could not be applied: {input_data.bookmark_id}",
                        "app_id": input_data.app_id,
                        "hint": "List valid bookmark ids with qlik_list_bookmarks.",
                    }
                bookmark_applied = True

            # Apply filters if provided; remember any the engine could not match.
            unmatched: list[dict] = []
            for f in input_data.filters or []:
                matched = await session.apply_selections(f.field, f.values)
                logger.debug("Applied filter: %s = %s (matched=%s)", f.field, f.values, matched)
                if not matched:
                    unmatched.append({"field": f.field, "values": f.values})

            # Create and fetch hypercube
            result = await session.create_hypercube(
                dimensions=input_data.dimensions,
                measures=input_data.measures,
                max_rows=effective_max,
            )

            payload: dict = {
                "app_id": input_data.app_id,
                "headers": result.headers,
                "data": result.rows,
                "row_count": len(result.rows),
                "total_rows": result.total_rows,
                "truncated": result.truncated,
                "filters_applied": [
                    {"field": f.field, "values": f.values}
                    for f in (input_data.filters or [])
                    if {"field": f.field, "values": f.values} not in unmatched
                ],
                "filters_not_matched": unmatched,
                "bookmark_id": input_data.bookmark_id,
                "bookmark_applied": bookmark_applied,
                "table": result.to_table(),
            }
            if unmatched:
                payload["warning"] = (
                    "Some filter values did not match any data and were not applied: "
                    + ", ".join(f"{u['field']}={u['values']}" for u in unmatched)
                    + ". Use qlik_get_fields to check field names."
                )
            return payload

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


GET_HYPERCUBE_DATA_SPEC = ToolSpec(
    name="qlik_get_hypercube_data",
    title="Get governed data",
    description=TOOL_DESCRIPTION,
    input_model=GetHypercubeDataInput,
    run=lambda ctx, params: handle_get_hypercube_data(
        ctx.engine, params,
        max_rows_limit=ctx.config.tools.max_hypercube_rows,
        max_columns_limit=ctx.config.tools.max_hypercube_columns,
    ),
)
