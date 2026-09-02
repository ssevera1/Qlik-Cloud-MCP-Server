"""qlik_get_fields: discover the fields available in an app's data model.

Agents need real field names before they can build a hypercube or apply
a selection. This tool lists the user-visible fields with cardinality and
source tables so the agent can pick valid dimensions and filters.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..engine_client import EngineClient, EngineError

logger = logging.getLogger(__name__)


class GetFieldsInput(BaseModel):
    """Input schema for qlik_get_fields."""

    app_id: str = Field(description="The Qlik Cloud app ID whose data model to inspect")


TOOL_DESCRIPTION = (
    "List the fields in a Qlik Cloud app's data model with their cardinality, "
    "tags, and source tables. Call this before qlik_get_hypercube_data to learn "
    "the exact field names to use as dimensions, in measures, or in filters."
)


async def handle_get_fields(engine: EngineClient, params: dict) -> dict:
    """Execute the qlik_get_fields tool."""
    input_data = GetFieldsInput(**params)

    try:
        async with engine.open_app(input_data.app_id) as session:
            fields = await session.get_fields()
            return {
                "app_id": input_data.app_id,
                "field_count": len(fields),
                "fields": fields,
            }

    except EngineError as e:
        logger.error("Engine error in get_fields: %s", e)
        return {
            "error": str(e),
            "app_id": input_data.app_id,
            "hint": "Verify the app_id exists and the service account has access.",
        }
