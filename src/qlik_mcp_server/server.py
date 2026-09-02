"""MCP server definition and tool registration.

Built on the MCP Python SDK v2 high-level ``MCPServer``. Each tool in the
registry is exposed with a flat, typed signature generated from its Pydantic
input model (so agents see a plain JSON schema) and returns a JSON object,
which the SDK emits as both text and structured content.
"""

from __future__ import annotations

import inspect
import logging
from typing import Annotated, Any, Awaitable, Callable, Literal, Optional, cast

from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError
from pydantic.fields import FieldInfo

from . import __version__
from .auth import AuthError, AuthManager
from .config import Config
from .engine_client import EngineClient, EngineError
from .qlik_cloud_client import QlikCloudClient, QlikCloudError
from .tools.registry import TOOL_NAMES, TOOL_SPECS, enabled_specs
from .tools.spec import ToolContext, ToolSpec

__all__ = ["TOOL_NAMES", "TOOL_SPECS", "create_server", "run_server", "transport_security_for"]

logger = logging.getLogger(__name__)

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_LOG_LEVELS: tuple[LogLevel, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

SERVER_INSTRUCTIONS = (
    "Tools for working with Qlik Cloud analytics apps. Typical flow: qlik_search to find an app "
    "(use its resource_id as app_id), qlik_describe_app for an overview, qlik_get_fields and "
    "qlik_list_measures to learn field names and governed measure definitions, qlik_list_sheets "
    "and qlik_get_sheet_details to see existing dashboards, qlik_get_chart_data to read a chart "
    "as shown, qlik_get_hypercube_data to compute governed aggregated data (optionally under a "
    "bookmark or filters), and qlik_create_sheet / qlik_add_chart / qlik_add_filter to build "
    "dashboards when nothing existing answers the question. All data access is governed by "
    "Qlik Section Access."
)

_CONSTRAINT_KWARGS = {
    Ge: "ge", Gt: "gt", Le: "le", Lt: "lt", MinLen: "min_length", MaxLen: "max_length",
}


def _field_meta(src: FieldInfo) -> Any:
    """Copy a model field's description and constraints into a fresh Field for a signature."""
    kwargs: dict[str, Any] = {"description": src.description}
    for constraint in src.metadata:
        for cls, kw in _CONSTRAINT_KWARGS.items():
            if isinstance(constraint, cls):
                kwargs[kw] = getattr(constraint, kw)
    return Field(**kwargs)


def _plain(value: Any) -> Any:
    """Turn validated argument values (which may be Pydantic models) into plain data."""
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


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


def _make_tool_function(spec: ToolSpec, ctx: ToolContext) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build an async function whose signature mirrors the tool's Pydantic input model.

    The SDK derives the JSON Schema from ``inspect.signature``, so the model's
    field types, descriptions, constraints, and defaults become the schema.
    The model itself is still applied inside the handler for custom validators.
    """
    parameters = []
    for name, model_field in spec.input_model.model_fields.items():
        # model_field.annotation is a runtime type object, not a literal type
        # expression; mypy still tries to statically resolve it as one inside
        # Annotated[...] and reports a spurious "Name is not defined".
        annotation = Annotated[model_field.annotation, _field_meta(model_field)]  # type: ignore[name-defined]
        if model_field.is_required():
            default = inspect.Parameter.empty
        else:
            default = model_field.get_default(call_default_factory=True)
        parameters.append(inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation,
        ))

    async def tool(**kwargs: Any) -> dict[str, Any]:
        payload = {k: _plain(v) for k, v in kwargs.items()}
        return await _guarded(spec.name, lambda: spec.run(ctx, payload))

    tool.__name__ = spec.name
    tool.__qualname__ = spec.name
    tool.__doc__ = spec.description
    tool.__signature__ = inspect.Signature(parameters, return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    return tool


def create_server(
    config: Config,
    qlik_client: Optional[QlikCloudClient] = None,
    engine_client: Optional[EngineClient] = None,
) -> MCPServer:
    """Create and configure the MCP server with the enabled Qlik tools."""
    auth = AuthManager(config)
    ctx = ToolContext(
        config=config,
        qlik_client=qlik_client or QlikCloudClient(config, auth),
        engine=engine_client or EngineClient(config, auth),
    )

    log_level_choice = config.server.log_level.upper()
    log_level: LogLevel = (
        cast(LogLevel, log_level_choice) if log_level_choice in _LOG_LEVELS else "INFO"
    )
    mcp = MCPServer(
        "qlik-cloud-mcp-server",
        title="Qlik Cloud MCP Server",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        log_level=log_level,
    )

    read_only = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False,
    )
    writes_app = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False,
    )

    for spec in enabled_specs(config):
        mcp.add_tool(
            _make_tool_function(spec, ctx),
            name=spec.name,
            title=spec.title,
            description=spec.description,
            annotations=writes_app if spec.writes else read_only,
        )

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
