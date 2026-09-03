"""qlik_select_values, qlik_clear_selections, qlik_get_current_selections.

These act on the app's engine session, which stays open between calls (see
``qlik.reuse_sessions``). Selections persist until cleared or until the
session idles out, exactly like a user working in the app. Per-call
``filters`` on the data tools never touch this state.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineError
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)


class SelectValuesInput(BaseModel):
    """Input schema for qlik_select_values."""

    app_id: str = Field(description="The Qlik Cloud app ID")
    field: str = Field(description="Field to select in (see qlik_get_fields)", min_length=1, max_length=256)
    values: Optional[list[str]] = Field(
        default=None, max_length=1000,
        description="Exact values to select, e.g. ['East', 'West']. Use this or 'match'.",
    )
    match: Optional[str] = Field(
        default=None, max_length=256,
        description=(
            "Qlik search pattern instead of exact values: wildcards ('Ea*', '*land'), "
            "numeric ranges ('>100', '>=2024<2026'), or plain text for a contains-match."
        ),
    )
    toggle: bool = Field(
        default=False,
        description="When true, toggle the given values in and out of the current selection instead of replacing it",
    )


class ClearSelectionsInput(BaseModel):
    """Input schema for qlik_clear_selections."""

    app_id: str = Field(description="The Qlik Cloud app ID")
    fields: Optional[list[str]] = Field(
        default=None, max_length=100,
        description="Fields to clear; omit to clear every selection in the app session",
    )


class CurrentSelectionsInput(BaseModel):
    """Input schema for qlik_get_current_selections."""

    app_id: str = Field(description="The Qlik Cloud app ID")


SELECT_VALUES_DESCRIPTION = (
    "Select values in a field of a Qlik Cloud app session, by exact values or by a search "
    "pattern (wildcards, numeric ranges, contains). Selections persist for later calls on the "
    "same app until qlik_clear_selections, so subsequent data tools reflect them. Use this to "
    "explore interactively; for a one-off filtered query pass 'filters' to qlik_create_data_object instead."
)

CLEAR_SELECTIONS_DESCRIPTION = (
    "Clear selections in a Qlik Cloud app session, for specific fields or all fields. "
    "Call this when you are done exploring so later calls see unfiltered data."
)

CURRENT_SELECTIONS_DESCRIPTION = (
    "Show the selections currently active in a Qlik Cloud app session: each selected field with "
    "its selected values and counts. Check this before interpreting data from other tools."
)


def _engine_error(name: str, e: EngineError, app_id: str, hint: str) -> dict:
    logger.error("Engine error in %s: %s", name, e)
    return {"error": str(e), "app_id": app_id, "hint": hint}


async def handle_select_values(ctx: ToolContext, params: dict) -> dict:
    input_data = SelectValuesInput(**params)
    if not input_data.values and not input_data.match:
        return {"error": "Provide either 'values' or 'match'", "app_id": input_data.app_id}
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            result = await session.select_values(
                input_data.field, values=input_data.values, match=input_data.match, toggle=input_data.toggle,
            )
            selections = await session.get_current_selections()
            payload = {"app_id": input_data.app_id, **result, "current_selections": selections}
            if not result["applied"]:
                payload["warning"] = "Nothing matched; the selection was not applied. Check values with qlik_get_field_values."
            return payload
    except EngineError as e:
        return _engine_error("select_values", e, input_data.app_id,
                             "Check the field name with qlik_get_fields.")


async def handle_clear_selections(ctx: ToolContext, params: dict) -> dict:
    input_data = ClearSelectionsInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            result = await session.clear_selections(input_data.fields)
            return {"app_id": input_data.app_id, **result}
    except EngineError as e:
        return _engine_error("clear_selections", e, input_data.app_id,
                             "Check the field names with qlik_get_fields.")


async def handle_get_current_selections(ctx: ToolContext, params: dict) -> dict:
    input_data = CurrentSelectionsInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            selections = await session.get_current_selections()
            return {"app_id": input_data.app_id, "selection_count": len(selections), "selections": selections}
    except EngineError as e:
        return _engine_error("get_current_selections", e, input_data.app_id,
                             "Verify the app_id exists and the service account has access.")


SELECT_VALUES_SPEC = ToolSpec(
    name="qlik_select_values", title="Select values", description=SELECT_VALUES_DESCRIPTION,
    input_model=SelectValuesInput, run=handle_select_values, stateful=True, group="selections",
)
CLEAR_SELECTIONS_SPEC = ToolSpec(
    name="qlik_clear_selections", title="Clear selections", description=CLEAR_SELECTIONS_DESCRIPTION,
    input_model=ClearSelectionsInput, run=handle_clear_selections, stateful=True, group="selections",
)
GET_CURRENT_SELECTIONS_SPEC = ToolSpec(
    name="qlik_get_current_selections", title="Get current selections",
    description=CURRENT_SELECTIONS_DESCRIPTION, input_model=CurrentSelectionsInput,
    run=handle_get_current_selections, group="selections",
)
