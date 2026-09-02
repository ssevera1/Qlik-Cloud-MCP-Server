"""The ordered catalog of MCP tools this server can expose."""

from __future__ import annotations

from .app_info import DESCRIBE_APP_SPEC, LIST_SHEETS_SPEC
from .chart import GET_CHART_DATA_SPEC, GET_CHART_INFO_SPEC
from .create_sheet import CREATE_SHEET_SPEC
from .field_values import GET_FIELD_VALUES_SPEC, SEARCH_FIELD_VALUES_SPEC
from .get_fields import GET_FIELDS_SPEC
from .get_hypercube_data import GET_HYPERCUBE_DATA_SPEC
from .get_sheet_details import GET_SHEET_DETAILS_SPEC
from .master_items import LIST_BOOKMARKS_SPEC, LIST_DIMENSIONS_SPEC, LIST_MEASURES_SPEC
from .search import SEARCH_SPEC
from .sheet_edit import ADD_CHART_SPEC, ADD_FILTER_SPEC
from .spec import ToolSpec

# Order matters: this is the order agents see, arranged as a workflow
# (discover, understand the model, inspect dashboards, compute, build).
TOOL_SPECS: tuple[ToolSpec, ...] = (
    SEARCH_SPEC,
    DESCRIBE_APP_SPEC,
    GET_FIELDS_SPEC,
    GET_FIELD_VALUES_SPEC,
    SEARCH_FIELD_VALUES_SPEC,
    LIST_SHEETS_SPEC,
    GET_SHEET_DETAILS_SPEC,
    GET_CHART_INFO_SPEC,
    GET_CHART_DATA_SPEC,
    LIST_DIMENSIONS_SPEC,
    LIST_MEASURES_SPEC,
    LIST_BOOKMARKS_SPEC,
    GET_HYPERCUBE_DATA_SPEC,
    CREATE_SHEET_SPEC,
    ADD_CHART_SPEC,
    ADD_FILTER_SPEC,
)

TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in TOOL_SPECS)


def enabled_specs(config) -> list[ToolSpec]:
    """Tools to register given the configuration (disabled list, legacy flags, write gate)."""
    return [
        spec for spec in TOOL_SPECS
        if config.tools.is_enabled(spec.name)
        and (not spec.writes or config.tools.allow_sheet_creation)
    ]
