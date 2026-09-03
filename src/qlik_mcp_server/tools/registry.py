"""The ordered catalog of MCP tools this server can expose."""

from __future__ import annotations

from .app_info import DESCRIBE_APP_SPEC, GET_APP_SCRIPT_SPEC, LIST_SHEETS_SPEC
from .chart import GET_CHART_DATA_SPEC, GET_CHART_INFO_SPEC
from .create_sheet import CREATE_SHEET_SPEC
from .field_values import GET_FIELD_VALUES_SPEC, SEARCH_FIELD_VALUES_SPEC
from .get_fields import GET_FIELDS_SPEC
from .get_hypercube_data import CREATE_DATA_OBJECT_SPEC
from .get_sheet_details import GET_SHEET_DETAILS_SPEC
from .master_items import (
    CREATE_BOOKMARK_SPEC,
    CREATE_DIMENSION_SPEC,
    CREATE_MEASURE_SPEC,
    DELETE_BOOKMARK_SPEC,
    DELETE_DIMENSION_SPEC,
    DELETE_MEASURE_SPEC,
    LIST_BOOKMARKS_SPEC,
    LIST_DIMENSIONS_SPEC,
    LIST_MEASURES_SPEC,
    SELECT_BOOKMARK_SPEC,
    UPDATE_DIMENSION_SPEC,
    UPDATE_MEASURE_SPEC,
)
from .search import SEARCH_SPEC
from .selections import CLEAR_SELECTIONS_SPEC, GET_CURRENT_SELECTIONS_SPEC, SELECT_VALUES_SPEC
from .sheet_edit import ADD_CHART_SPEC, ADD_FILTER_SPEC
from .rest_catalog import REST_TOOL_SPECS
from .spec import ToolSpec

# Engine-backed tools, arranged as a workflow: discover, understand the model,
# inspect dashboards, explore interactively, compute, build.
ENGINE_TOOL_SPECS: tuple[ToolSpec, ...] = (
    SEARCH_SPEC,
    DESCRIBE_APP_SPEC,
    GET_APP_SCRIPT_SPEC,
    GET_FIELDS_SPEC,
    GET_FIELD_VALUES_SPEC,
    SEARCH_FIELD_VALUES_SPEC,
    LIST_SHEETS_SPEC,
    GET_SHEET_DETAILS_SPEC,
    GET_CHART_INFO_SPEC,
    GET_CHART_DATA_SPEC,
    LIST_DIMENSIONS_SPEC,
    LIST_MEASURES_SPEC,
    CREATE_DIMENSION_SPEC,
    UPDATE_DIMENSION_SPEC,
    DELETE_DIMENSION_SPEC,
    CREATE_MEASURE_SPEC,
    UPDATE_MEASURE_SPEC,
    DELETE_MEASURE_SPEC,
    LIST_BOOKMARKS_SPEC,
    CREATE_BOOKMARK_SPEC,
    SELECT_BOOKMARK_SPEC,
    DELETE_BOOKMARK_SPEC,
    SELECT_VALUES_SPEC,
    CLEAR_SELECTIONS_SPEC,
    GET_CURRENT_SELECTIONS_SPEC,
    CREATE_DATA_OBJECT_SPEC,
    CREATE_SHEET_SPEC,
    ADD_CHART_SPEC,
    ADD_FILTER_SPEC,
)

TOOL_SPECS: tuple[ToolSpec, ...] = ENGINE_TOOL_SPECS + tuple(REST_TOOL_SPECS)

TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in TOOL_SPECS)

# Sheet-building tools are additionally gated by tools.allow_sheet_creation.
SHEET_TOOLS = {"qlik_create_sheet", "qlik_add_chart", "qlik_add_filter"}


def tool_groups() -> dict[str, list[str]]:
    """Group name to tool names, in registry order."""
    groups: dict[str, list[str]] = {}
    for spec in TOOL_SPECS:
        groups.setdefault(spec.group, []).append(spec.name)
    return groups


def enabled_specs(config) -> list[ToolSpec]:
    """Tools to register given the configuration (disabled lists, legacy flags, write gates)."""
    result = []
    for spec in TOOL_SPECS:
        if not config.tools.is_enabled(spec.name, spec.group):
            continue
        if spec.writes and not config.tools.writes_allowed:
            continue
        if spec.name in SHEET_TOOLS and not config.tools.allow_sheet_creation:
            continue
        result.append(spec)
    return result
