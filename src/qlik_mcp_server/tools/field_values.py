"""qlik_get_field_values and qlik_search_field_values: find the exact values to filter on."""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineError
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)


class GetFieldValuesInput(BaseModel):
    """Input schema for qlik_get_field_values."""

    app_id: str = Field(description="The Qlik Cloud app ID")
    field: str = Field(description="Field name to list values for (see qlik_get_fields)", min_length=1, max_length=256)
    max_values: Optional[int] = Field(
        default=100, ge=1, le=10000,
        description="Maximum number of distinct values to return (default 100, max 10000)",
    )
    match: Optional[str] = Field(
        default=None, max_length=256,
        description="Optional search text; only values containing it are returned (case-insensitive)",
    )


class SearchFieldValuesInput(BaseModel):
    """Input schema for qlik_search_field_values."""

    app_id: str = Field(description="The Qlik Cloud app ID")
    terms: list[str] = Field(
        description="Search terms, e.g. ['east', '2025']. Each term is matched against field values.",
        min_length=1, max_length=10,
    )
    fields: Optional[list[str]] = Field(
        default=None, max_length=50,
        description="Optional list of field names to restrict the search to; omit to search all fields",
    )
    max_matches_per_field: Optional[int] = Field(
        default=10, ge=1, le=100,
        description="Maximum matching values to return per field (default 10)",
    )


GET_FIELD_VALUES_DESCRIPTION = (
    "List the distinct values of a field in a Qlik Cloud app with each value's frequency. "
    "Use this to find the exact spelling of values before passing them as filters to "
    "qlik_get_hypercube_data. Supports an optional substring match to narrow long lists."
)

SEARCH_FIELD_VALUES_DESCRIPTION = (
    "Search across all field values in a Qlik Cloud app for one or more terms and return which "
    "fields contain matching values. Use this when you know a value (a customer, product, city) "
    "but not which field holds it."
)


async def handle_get_field_values(ctx: ToolContext, params: dict) -> dict:
    """Execute the qlik_get_field_values tool."""
    input_data = GetFieldValuesInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            result = await session.get_field_values(
                input_data.field, max_values=input_data.max_values or 100, match=input_data.match,
            )
            return {"app_id": input_data.app_id, "match": input_data.match, **result}
    except EngineError as e:
        logger.error("Engine error in get_field_values: %s", e)
        return {"error": str(e), "app_id": input_data.app_id, "field": input_data.field,
                "hint": "Check the field name with qlik_get_fields."}


async def handle_search_field_values(ctx: ToolContext, params: dict) -> dict:
    """Execute the qlik_search_field_values tool."""
    input_data = SearchFieldValuesInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            result = await session.search_field_values(
                input_data.terms, fields=input_data.fields,
                max_matches=input_data.max_matches_per_field or 10,
            )
            return {"app_id": input_data.app_id, **result}
    except EngineError as e:
        logger.error("Engine error in search_field_values: %s", e)
        return {"error": str(e), "app_id": input_data.app_id,
                "hint": "Verify the app_id and any field names passed in 'fields'."}


GET_FIELD_VALUES_SPEC = ToolSpec(
    name="qlik_get_field_values",
    title="Get field values",
    description=GET_FIELD_VALUES_DESCRIPTION,
    input_model=GetFieldValuesInput,
    run=handle_get_field_values,
)

SEARCH_FIELD_VALUES_SPEC = ToolSpec(
    name="qlik_search_field_values",
    title="Search field values",
    description=SEARCH_FIELD_VALUES_DESCRIPTION,
    input_model=SearchFieldValuesInput,
    run=handle_search_field_values,
)
