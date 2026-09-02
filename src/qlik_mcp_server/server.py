"""MCP server definition and tool registration.

Built on the MCP Python SDK v2 high-level ``MCPServer``. Each Qlik tool is
registered with a flat, typed signature (so agents see a plain JSON schema)
and returns a JSON object, which the SDK emits as both text and structured
content.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Awaitable, Callable, Optional

from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from . import __version__
from .auth import AuthError, AuthManager
from .config import Config
from .engine_client import EngineClient, EngineError
from .qlik_cloud_client import QlikCloudClient, QlikCloudError
from .tools.create_sheet import (
    TOOL_DESCRIPTION as CREATE_SHEET_DESC,
    CreateSheetInput,
    VisualizationObject,
    handle_create_sheet,
)
from .tools.get_fields import (
    TOOL_DESCRIPTION as GET_FIELDS_DESC,
    GetFieldsInput,
    handle_get_fields,
)
from .tools.get_hypercube_data import (
    TOOL_DESCRIPTION as HYPERCUBE_DESC,
    Filter,
    GetHypercubeDataInput,
    handle_get_hypercube_data,
)
from .tools.get_sheet_details import (
    TOOL_DESCRIPTION as SHEET_DETAILS_DESC,
    GetSheetDetailsInput,
    handle_get_sheet_details,
)
from .tools.search import (
    TOOL_DESCRIPTION as SEARCH_DESC,
    SearchInput,
    handle_search,
)

logger = logging.getLogger(__name__)

TOOL_NAMES = (
    "qlik_search",
    "qlik_get_fields",
    "qlik_get_sheet_details",
    "qlik_get_hypercube_data",
    "qlik_create_sheet",
)

SERVER_INSTRUCTIONS = (
    "Tools for working with Qlik Cloud analytics apps. Typical flow: "
    "qlik_search to find an app (use its resource_id as app_id), "
    "qlik_get_fields to learn the field names, qlik_get_sheet_details to see "
    "existing dashboards, qlik_get_hypercube_data to retrieve governed aggregated "
    "data, and qlik_create_sheet to build a new sheet when nothing existing answers "
    "the question. All data access is governed by Qlik Section Access."
)


_CONSTRAINT_KWARGS = {
    Ge: "ge", Gt: "gt", Le: "le", Lt: "lt", MinLen: "min_length", MaxLen: "max_length",
}


def _field(model: type, name: str) -> Any:
    """Reuse a Pydantic model's field description and constraints in a tool signature.

    Defaults are deliberately not copied: the tool function's own default
    is the single source of truth for the generated JSON schema.
    """
    src = model.model_fields[name]
    kwargs: dict[str, Any] = {"description": src.description}
    for constraint in src.metadata:
        for cls, kw in _CONSTRAINT_KWARGS.items():
            if isinstance(constraint, cls):
                kwargs[kw] = getattr(constraint, kw)
    return Field(**kwargs)


def _validation_error_payload(e: ValidationError, **context: Any) -> dict:
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', ())) or 'input'}: {err.get('msg', '')}"
        for err in e.errors()
    )
    return {"error": f"Invalid input: {problems}", **context}


async def _guarded(tool_name: str, call: Callable[[], Awaitable[dict]]) -> dict:
    """Run a tool handler, converting failures into agent-readable error payloads."""
    try:
        return await call()
    except ValidationError as e:
        return _validation_error_payload(e, tool=tool_name)
    except (EngineError, QlikCloudError) as e:
        logger.error("Tool %s failed: %s", tool_name, e)
        return {"error": str(e), "tool": tool_name}
    except AuthError as e:
        logger.error("Tool %s authentication failed: %s", tool_name, e)
        return {"error": "Authentication with Qlik Cloud failed", "tool": tool_name,
                "hint": "Check the API key or OAuth credentials in the server configuration."}
    except Exception as e:  # noqa: BLE001 - never leak internals to the agent
        logger.error("Tool %s crashed: %s", tool_name, e, exc_info=True)
        return {"error": "An internal error occurred", "tool": tool_name,
                "hint": "Check the server logs for details."}


def create_server(
    config: Config,
    qlik_client: Optional[QlikCloudClient] = None,
    engine_client: Optional[EngineClient] = None,
) -> MCPServer:
    """Create and configure the MCP server with the enabled Qlik tools."""
    auth = AuthManager(config)
    qlik_client = qlik_client or QlikCloudClient(config, auth)
    engine_client = engine_client or EngineClient(config, auth)

    mcp = MCPServer(
        "qlik-cloud-mcp-server",
        title="Qlik Cloud MCP Server",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        log_level=config.server.log_level.upper()
        if config.server.log_level.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        else "INFO",
    )

    read_only = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
    writes_app = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

    # ── qlik_search ───────────────────────────────────────────────
    if config.tools.search:
        async def qlik_search(
            query: Annotated[str, _field(SearchInput, "query")],
            resource_type: Annotated[Optional[str], _field(SearchInput, "resource_type")] = None,
            space: Annotated[Optional[str], _field(SearchInput, "space")] = None,
            limit: Annotated[Optional[int], _field(SearchInput, "limit")] = 20,
        ) -> dict[str, Any]:
            return await _guarded("qlik_search", lambda: handle_search(qlik_client, {
                "query": query, "resource_type": resource_type, "space": space, "limit": limit,
            }))

        mcp.add_tool(qlik_search, name="qlik_search", title="Search Qlik catalog",
                     description=SEARCH_DESC, annotations=read_only)

    # ── qlik_get_fields ───────────────────────────────────────────
    if config.tools.get_fields:
        async def qlik_get_fields(
            app_id: Annotated[str, _field(GetFieldsInput, "app_id")],
        ) -> dict[str, Any]:
            return await _guarded("qlik_get_fields", lambda: handle_get_fields(
                engine_client, {"app_id": app_id},
            ))

        mcp.add_tool(qlik_get_fields, name="qlik_get_fields", title="List app fields",
                     description=GET_FIELDS_DESC, annotations=read_only)

    # ── qlik_get_sheet_details ────────────────────────────────────
    if config.tools.get_sheet_details:
        async def qlik_get_sheet_details(
            app_id: Annotated[str, _field(GetSheetDetailsInput, "app_id")],
            sheet_id: Annotated[Optional[str], _field(GetSheetDetailsInput, "sheet_id")] = None,
        ) -> dict[str, Any]:
            return await _guarded("qlik_get_sheet_details", lambda: handle_get_sheet_details(
                engine_client, {"app_id": app_id, "sheet_id": sheet_id},
            ))

        mcp.add_tool(qlik_get_sheet_details, name="qlik_get_sheet_details",
                     title="Inspect sheets", description=SHEET_DETAILS_DESC, annotations=read_only)

    # ── qlik_get_hypercube_data ───────────────────────────────────
    if config.tools.get_hypercube_data:
        async def qlik_get_hypercube_data(
            app_id: Annotated[str, _field(GetHypercubeDataInput, "app_id")],
            dimensions: Annotated[list[str], _field(GetHypercubeDataInput, "dimensions")],
            measures: Annotated[list[str], _field(GetHypercubeDataInput, "measures")],
            filters: Annotated[Optional[list[Filter]], _field(GetHypercubeDataInput, "filters")] = None,
            max_rows: Annotated[Optional[int], _field(GetHypercubeDataInput, "max_rows")] = 1000,
        ) -> dict[str, Any]:
            return await _guarded("qlik_get_hypercube_data", lambda: handle_get_hypercube_data(
                engine_client,
                {
                    "app_id": app_id,
                    "dimensions": dimensions,
                    "measures": measures,
                    "filters": [f.model_dump() for f in filters] if filters else None,
                    "max_rows": max_rows,
                },
                max_rows_limit=config.tools.max_hypercube_rows,
                max_columns_limit=config.tools.max_hypercube_columns,
            ))

        mcp.add_tool(qlik_get_hypercube_data, name="qlik_get_hypercube_data",
                     title="Get governed data", description=HYPERCUBE_DESC, annotations=read_only)

    # ── qlik_create_sheet ─────────────────────────────────────────
    if config.tools.create_sheet:
        async def qlik_create_sheet(
            app_id: Annotated[str, _field(CreateSheetInput, "app_id")],
            title: Annotated[str, _field(CreateSheetInput, "title")],
            description: Annotated[Optional[str], _field(CreateSheetInput, "description")] = "",
            objects: Annotated[Optional[list[VisualizationObject]], _field(CreateSheetInput, "objects")] = None,
        ) -> dict[str, Any]:
            return await _guarded("qlik_create_sheet", lambda: handle_create_sheet(
                engine_client,
                {
                    "app_id": app_id,
                    "title": title,
                    "description": description,
                    "objects": [o.model_dump() for o in (objects or [])],
                },
                sheet_prefix=config.tools.created_sheet_prefix,
                allow_creation=config.tools.allow_sheet_creation,
            ))

        mcp.add_tool(qlik_create_sheet, name="qlik_create_sheet", title="Create sheet",
                     description=CREATE_SHEET_DESC, annotations=writes_app)

    return mcp


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def transport_security_for(config: Config) -> Optional[TransportSecuritySettings]:
    """DNS-rebinding protection for the HTTP transports.

    On a loopback bind, only Host headers naming localhost are accepted so a
    malicious web page cannot reach the server through the browser. On any
    other bind the deployment is expected to sit behind an authenticating
    proxy that owns host validation, so the SDK default (off) is kept.
    """
    host = config.server.http_host
    if host not in _LOOPBACK_HOSTS:
        return None
    port = config.server.http_port
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{h}:{port}" for h in _LOOPBACK_HOSTS] + list(_LOOPBACK_HOSTS),
        allowed_origins=[f"http://{h}:{port}" for h in _LOOPBACK_HOSTS],
    )


def run_server(config: Config) -> None:
    """Run the MCP server on the configured transport (blocking)."""
    mcp = create_server(config)
    transport = config.server.transport

    if transport == "stdio":
        logger.info("Starting Qlik Cloud MCP Server (stdio), tenant: %s", config.tenant_host)
        mcp.run("stdio")
    elif transport == "streamable-http":
        logger.info(
            "Starting Qlik Cloud MCP Server (Streamable HTTP) on http://%s:%d%s, tenant: %s",
            config.server.http_host, config.server.http_port, config.server.http_path,
            config.tenant_host,
        )
        mcp.run(
            "streamable-http",
            host=config.server.http_host,
            port=config.server.http_port,
            streamable_http_path=config.server.http_path,
            transport_security=transport_security_for(config),
        )
    elif transport == "sse":
        logger.warning(
            "SSE transport is deprecated in the MCP specification; prefer streamable-http."
        )
        logger.info(
            "Starting Qlik Cloud MCP Server (SSE) on http://%s:%d/sse, tenant: %s",
            config.server.http_host, config.server.http_port, config.tenant_host,
        )
        mcp.run(
            "sse",
            host=config.server.http_host,
            port=config.server.http_port,
            transport_security=transport_security_for(config),
        )
    else:
        raise ValueError(f"Unknown transport: {transport}")
