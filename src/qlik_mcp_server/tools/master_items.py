"""qlik_list_dimensions, qlik_list_measures, qlik_list_bookmarks: governed definitions."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..engine_client import EngineError
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)


class AppOnlyInput(BaseModel):
    """Input schema for tools that only need an app id."""

    app_id: str = Field(description="The Qlik Cloud app ID")


LIST_DIMENSIONS_DESCRIPTION = (
    "List the master (library) dimensions defined in a Qlik Cloud app with their titles, "
    "descriptions, tags, and underlying field definitions. Prefer these governed definitions "
    "as dimensions in qlik_get_hypercube_data when they exist."
)

LIST_MEASURES_DESCRIPTION = (
    "List the master (library) measures defined in a Qlik Cloud app with their titles, "
    "descriptions, tags, and expressions. Use the expression text as a measure in "
    "qlik_get_hypercube_data so results match the business's governed definitions."
)

LIST_BOOKMARKS_DESCRIPTION = (
    "List the bookmarks saved in a Qlik Cloud app (named selection sets) with their ids, titles, "
    "and the fields they select. Pass a bookmark id as bookmark_id to qlik_get_hypercube_data "
    "to compute data under that bookmark's selections."
)


def _engine_error(name: str, e: EngineError, app_id: str) -> dict:
    logger.error("Engine error in %s: %s", name, e)
    return {"error": str(e), "app_id": app_id,
            "hint": "Verify the app_id exists and the service account has access."}


async def handle_list_dimensions(ctx: ToolContext, params: dict) -> dict:
    input_data = AppOnlyInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            items = await session.get_master_items()
            return {"app_id": input_data.app_id, "dimension_count": len(items["dimensions"]),
                    "dimensions": items["dimensions"]}
    except EngineError as e:
        return _engine_error("list_dimensions", e, input_data.app_id)


async def handle_list_measures(ctx: ToolContext, params: dict) -> dict:
    input_data = AppOnlyInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            items = await session.get_master_items()
            return {"app_id": input_data.app_id, "measure_count": len(items["measures"]),
                    "measures": items["measures"]}
    except EngineError as e:
        return _engine_error("list_measures", e, input_data.app_id)


async def handle_list_bookmarks(ctx: ToolContext, params: dict) -> dict:
    input_data = AppOnlyInput(**params)
    try:
        async with ctx.engine.open_app(input_data.app_id) as session:
            bookmarks = await session.get_bookmarks()
            return {"app_id": input_data.app_id, "bookmark_count": len(bookmarks), "bookmarks": bookmarks}
    except EngineError as e:
        return _engine_error("list_bookmarks", e, input_data.app_id)


LIST_DIMENSIONS_SPEC = ToolSpec(
    name="qlik_list_dimensions",
    title="List master dimensions",
    description=LIST_DIMENSIONS_DESCRIPTION,
    input_model=AppOnlyInput,
    run=handle_list_dimensions,
)

LIST_MEASURES_SPEC = ToolSpec(
    name="qlik_list_measures",
    title="List master measures",
    description=LIST_MEASURES_DESCRIPTION,
    input_model=AppOnlyInput,
    run=handle_list_measures,
)

LIST_BOOKMARKS_SPEC = ToolSpec(
    name="qlik_list_bookmarks",
    title="List bookmarks",
    description=LIST_BOOKMARKS_DESCRIPTION,
    input_model=AppOnlyInput,
    run=handle_list_bookmarks,
)
