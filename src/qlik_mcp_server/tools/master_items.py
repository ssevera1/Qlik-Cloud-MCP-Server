"""Master items and bookmarks: list, create, update, delete, select."""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from ..engine_client import EngineError
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)


class AppOnlyInput(BaseModel):
    """Input schema for tools that only need an app id."""

    app_id: str = Field(description="The Qlik Cloud app ID")


class CreateDimensionInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    title: str = Field(description="Name of the master dimension", min_length=1, max_length=256)
    field_defs: list[str] = Field(
        description="Field names or expressions, e.g. ['Region'] or ['=Year(OrderDate)']; more than one makes a drill-down group",
        min_length=1, max_length=10,
    )
    label: Optional[str] = Field(default=None, max_length=256, description="Display label (defaults to the field name)")
    description: str = Field(default="", max_length=2000, description="Description shown in the library")
    tags: Optional[list[str]] = Field(default=None, max_length=20, description="Tags for the library item")


class UpdateDimensionInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    dimension_id: str = Field(description="Id of the master dimension (see qlik_list_dimensions)", min_length=1, max_length=256)
    title: Optional[str] = Field(default=None, max_length=256, description="New name")
    field_defs: Optional[list[str]] = Field(default=None, max_length=10, description="New field names or expressions")
    label: Optional[str] = Field(default=None, max_length=256, description="New display label")
    description: Optional[str] = Field(default=None, max_length=2000, description="New description")
    tags: Optional[list[str]] = Field(default=None, max_length=20, description="Replacement tag list")


class DeleteDimensionInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    dimension_id: str = Field(description="Id of the master dimension to delete", min_length=1, max_length=256)


class CreateMeasureInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    title: str = Field(description="Name of the master measure", min_length=1, max_length=256)
    expression: str = Field(description="Aggregation expression, e.g. 'Sum(Sales)' or 'Sum(Margin)/Sum(Sales)'", min_length=1, max_length=4000)
    label: Optional[str] = Field(default=None, max_length=256, description="Display label (defaults to the title)")
    description: str = Field(default="", max_length=2000, description="Description shown in the library")
    tags: Optional[list[str]] = Field(default=None, max_length=20, description="Tags for the library item")


class UpdateMeasureInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    measure_id: str = Field(description="Id of the master measure (see qlik_list_measures)", min_length=1, max_length=256)
    title: Optional[str] = Field(default=None, max_length=256, description="New name")
    expression: Optional[str] = Field(default=None, max_length=4000, description="New expression")
    label: Optional[str] = Field(default=None, max_length=256, description="New display label")
    description: Optional[str] = Field(default=None, max_length=2000, description="New description")
    tags: Optional[list[str]] = Field(default=None, max_length=20, description="Replacement tag list")


class DeleteMeasureInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    measure_id: str = Field(description="Id of the master measure to delete", min_length=1, max_length=256)


class CreateBookmarkInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    title: str = Field(description="Bookmark name", min_length=1, max_length=256)
    description: str = Field(default="", max_length=2000, description="Bookmark description")
    sheet_id: Optional[str] = Field(default=None, max_length=256, description="Sheet to open when the bookmark is applied")


class SelectBookmarkInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    bookmark_id: str = Field(description="Id of the bookmark to apply (see qlik_list_bookmarks)", min_length=1, max_length=256)


class DeleteBookmarkInput(BaseModel):
    app_id: str = Field(description="The Qlik Cloud app ID")
    bookmark_id: str = Field(description="Id of the bookmark to delete", min_length=1, max_length=256)


LIST_DIMENSIONS_DESCRIPTION = (
    "List the master (library) dimensions defined in a Qlik Cloud app with their titles, "
    "descriptions, tags, and underlying field definitions. Prefer these governed definitions "
    "as dimensions in qlik_create_data_object when they exist."
)
LIST_MEASURES_DESCRIPTION = (
    "List the master (library) measures defined in a Qlik Cloud app with their titles, "
    "descriptions, tags, and expressions. Use the expression text as a measure in "
    "qlik_create_data_object so results match the business's governed definitions."
)
LIST_BOOKMARKS_DESCRIPTION = (
    "List the bookmarks saved in a Qlik Cloud app (named selection sets) with their ids, titles, "
    "and the fields they select. Apply one with qlik_select_bookmark or pass its id as bookmark_id "
    "to qlik_create_data_object."
)
CREATE_DIMENSION_DESCRIPTION = "Create a reusable master dimension in a Qlik Cloud app and save the app."
UPDATE_DIMENSION_DESCRIPTION = "Update the name, fields, label, description, or tags of a master dimension and save the app. Only the given properties change."
DELETE_DIMENSION_DESCRIPTION = "Delete a master dimension from a Qlik Cloud app and save the app. Charts that use it will lose the dimension."
CREATE_MEASURE_DESCRIPTION = "Create a reusable master measure (a governed calculation) in a Qlik Cloud app and save the app."
UPDATE_MEASURE_DESCRIPTION = "Update the name, expression, label, description, or tags of a master measure and save the app. Only the given properties change."
DELETE_MEASURE_DESCRIPTION = "Delete a master measure from a Qlik Cloud app and save the app. Charts that use it will lose the measure."
CREATE_BOOKMARK_DESCRIPTION = (
    "Save the session's current selections (made with qlik_select_values) as a named bookmark in "
    "the app and save the app, so people and later calls can reapply them."
)
SELECT_BOOKMARK_DESCRIPTION = (
    "Apply a bookmark's selections to the Qlik Cloud app session and return the resulting "
    "selection state. Later data calls on the app reflect it until qlik_clear_selections."
)
DELETE_BOOKMARK_DESCRIPTION = "Delete a bookmark from a Qlik Cloud app and save the app."


def _engine_error(name: str, e: EngineError, app_id: str, hint: str = "") -> dict:
    logger.error("Engine error in %s: %s", name, e)
    return {"error": str(e), "app_id": app_id,
            "hint": hint or "Verify the app_id exists and the service account has access."}


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


_EDIT_HINT = "Verify the id and that the service account can edit the app."


async def handle_create_dimension(ctx: ToolContext, params: dict) -> dict:
    d = CreateDimensionInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            result = await session.create_dimension(
                d.title, field_defs=d.field_defs, label=d.label, description=d.description, tags=d.tags,
            )
            return {"app_id": d.app_id, **result}
    except EngineError as e:
        return _engine_error("create_dimension", e, d.app_id, _EDIT_HINT)


async def handle_update_dimension(ctx: ToolContext, params: dict) -> dict:
    d = UpdateDimensionInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            result = await session.update_dimension(
                d.dimension_id, title=d.title, field_defs=d.field_defs, label=d.label,
                description=d.description, tags=d.tags,
            )
            return {"app_id": d.app_id, **result}
    except EngineError as e:
        return _engine_error("update_dimension", e, d.app_id, _EDIT_HINT)


async def handle_delete_dimension(ctx: ToolContext, params: dict) -> dict:
    d = DeleteDimensionInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            ok = await session.delete_dimension(d.dimension_id)
            return {"app_id": d.app_id, "dimension_id": d.dimension_id, "deleted": ok, "saved": ok}
    except EngineError as e:
        return _engine_error("delete_dimension", e, d.app_id, _EDIT_HINT)


async def handle_create_measure(ctx: ToolContext, params: dict) -> dict:
    d = CreateMeasureInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            result = await session.create_measure(
                d.title, expression=d.expression, label=d.label, description=d.description, tags=d.tags,
            )
            return {"app_id": d.app_id, **result}
    except EngineError as e:
        return _engine_error("create_measure", e, d.app_id, _EDIT_HINT)


async def handle_update_measure(ctx: ToolContext, params: dict) -> dict:
    d = UpdateMeasureInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            result = await session.update_measure(
                d.measure_id, title=d.title, expression=d.expression, label=d.label,
                description=d.description, tags=d.tags,
            )
            return {"app_id": d.app_id, **result}
    except EngineError as e:
        return _engine_error("update_measure", e, d.app_id, _EDIT_HINT)


async def handle_delete_measure(ctx: ToolContext, params: dict) -> dict:
    d = DeleteMeasureInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            ok = await session.delete_measure(d.measure_id)
            return {"app_id": d.app_id, "measure_id": d.measure_id, "deleted": ok, "saved": ok}
    except EngineError as e:
        return _engine_error("delete_measure", e, d.app_id, _EDIT_HINT)


async def handle_create_bookmark(ctx: ToolContext, params: dict) -> dict:
    d = CreateBookmarkInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            result = await session.create_bookmark(d.title, description=d.description, sheet_id=d.sheet_id or "")
            return {"app_id": d.app_id, **result}
    except EngineError as e:
        return _engine_error("create_bookmark", e, d.app_id, _EDIT_HINT)


async def handle_select_bookmark(ctx: ToolContext, params: dict) -> dict:
    d = SelectBookmarkInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            ok = await session.apply_bookmark(d.bookmark_id)
            if not ok:
                return {"error": f"Bookmark not found or could not be applied: {d.bookmark_id}",
                        "app_id": d.app_id, "hint": "List valid bookmark ids with qlik_list_bookmarks."}
            selections = await session.get_current_selections()
            return {"app_id": d.app_id, "bookmark_id": d.bookmark_id, "applied": True,
                    "current_selections": selections}
    except EngineError as e:
        return _engine_error("select_bookmark", e, d.app_id)


async def handle_delete_bookmark(ctx: ToolContext, params: dict) -> dict:
    d = DeleteBookmarkInput(**params)
    try:
        async with ctx.engine.open_app(d.app_id) as session:
            ok = await session.delete_bookmark(d.bookmark_id)
            return {"app_id": d.app_id, "bookmark_id": d.bookmark_id, "deleted": ok, "saved": ok}
    except EngineError as e:
        return _engine_error("delete_bookmark", e, d.app_id, _EDIT_HINT)


LIST_DIMENSIONS_SPEC = ToolSpec(
    name="qlik_list_dimensions", title="List master dimensions", description=LIST_DIMENSIONS_DESCRIPTION,
    input_model=AppOnlyInput, run=handle_list_dimensions, group="master_items",
)
LIST_MEASURES_SPEC = ToolSpec(
    name="qlik_list_measures", title="List master measures", description=LIST_MEASURES_DESCRIPTION,
    input_model=AppOnlyInput, run=handle_list_measures, group="master_items",
)
CREATE_DIMENSION_SPEC = ToolSpec(
    name="qlik_create_dimension", title="Create master dimension", description=CREATE_DIMENSION_DESCRIPTION,
    input_model=CreateDimensionInput, run=handle_create_dimension, writes=True, group="master_items",
)
UPDATE_DIMENSION_SPEC = ToolSpec(
    name="qlik_update_dimension", title="Update master dimension", description=UPDATE_DIMENSION_DESCRIPTION,
    input_model=UpdateDimensionInput, run=handle_update_dimension, writes=True, group="master_items",
)
DELETE_DIMENSION_SPEC = ToolSpec(
    name="qlik_delete_dimension", title="Delete master dimension", description=DELETE_DIMENSION_DESCRIPTION,
    input_model=DeleteDimensionInput, run=handle_delete_dimension, writes=True, group="master_items",
)
CREATE_MEASURE_SPEC = ToolSpec(
    name="qlik_create_measure", title="Create master measure", description=CREATE_MEASURE_DESCRIPTION,
    input_model=CreateMeasureInput, run=handle_create_measure, writes=True, group="master_items",
)
UPDATE_MEASURE_SPEC = ToolSpec(
    name="qlik_update_measure", title="Update master measure", description=UPDATE_MEASURE_DESCRIPTION,
    input_model=UpdateMeasureInput, run=handle_update_measure, writes=True, group="master_items",
)
DELETE_MEASURE_SPEC = ToolSpec(
    name="qlik_delete_measure", title="Delete master measure", description=DELETE_MEASURE_DESCRIPTION,
    input_model=DeleteMeasureInput, run=handle_delete_measure, writes=True, group="master_items",
)
LIST_BOOKMARKS_SPEC = ToolSpec(
    name="qlik_list_bookmarks", title="List bookmarks", description=LIST_BOOKMARKS_DESCRIPTION,
    input_model=AppOnlyInput, run=handle_list_bookmarks, group="bookmarks",
)
CREATE_BOOKMARK_SPEC = ToolSpec(
    name="qlik_create_bookmark", title="Create bookmark", description=CREATE_BOOKMARK_DESCRIPTION,
    input_model=CreateBookmarkInput, run=handle_create_bookmark, writes=True, group="bookmarks",
)
SELECT_BOOKMARK_SPEC = ToolSpec(
    name="qlik_select_bookmark", title="Select bookmark", description=SELECT_BOOKMARK_DESCRIPTION,
    input_model=SelectBookmarkInput, run=handle_select_bookmark, stateful=True, group="bookmarks",
)
DELETE_BOOKMARK_SPEC = ToolSpec(
    name="qlik_delete_bookmark", title="Delete bookmark", description=DELETE_BOOKMARK_DESCRIPTION,
    input_model=DeleteBookmarkInput, run=handle_delete_bookmark, writes=True, group="bookmarks",
)
