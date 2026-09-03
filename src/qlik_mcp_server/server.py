"""MCP server definition and tool registration.

Built on the MCP Python SDK v2 high-level ``MCPServer``. Each tool in the
registry is exposed with a flat, typed signature generated from its Pydantic
input model (so agents see a plain JSON schema) and returns a JSON object,
which the SDK emits as both text and structured content.

Schemas are simplified after registration (no ``$ref``, ``$defs``, ``anyOf``
or ``title``) so they load in every client, including Gemini's stricter
function-declaration subset.
"""

from __future__ import annotations

import hmac
import inspect
import json
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

__all__ = [
    "TOOL_NAMES", "TOOL_SPECS", "build_http_app", "create_server", "run_server",
    "simplify_schema", "transport_security_for",
]

logger = logging.getLogger(__name__)

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_LOG_LEVELS: tuple[LogLevel, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

SERVER_INSTRUCTIONS = (
    "Tools for working with Qlik Cloud: analytics apps, data catalog, automations, governance, "
    "and AI services. Typical analytics flow: qlik_search to find an app (use its resource_id "
    "as app_id), qlik_describe_app for an overview, qlik_get_fields and qlik_list_measures for "
    "field names and governed measure definitions, qlik_list_sheets / qlik_get_sheet_details / "
    "qlik_get_chart_data to read existing dashboards, qlik_create_data_object to compute "
    "governed aggregated data (with per-call filters or a bookmark), qlik_select_values to "
    "explore interactively (selections persist on the app session until qlik_clear_selections), "
    "and qlik_create_sheet / qlik_add_chart / qlik_add_filter to build dashboards. All data "
    "access is governed by Qlik Section Access."
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


def _trim_payload(payload: dict, max_chars: int) -> dict:
    """Keep responses within a character budget by shortening long lists and strings."""
    if max_chars <= 0:
        return payload
    text = json.dumps(payload, default=str)
    if len(text) <= max_chars:
        return payload

    def shrink(value: Any, depth: int = 0) -> Any:
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000] + f"... [truncated {len(value) - 4000} chars]"
        if isinstance(value, list):
            if len(value) > 200:
                return [shrink(v, depth + 1) for v in value[:200]] + [f"... [{len(value) - 200} more items truncated]"]
            return [shrink(v, depth + 1) for v in value]
        if isinstance(value, dict):
            return {k: shrink(v, depth + 1) for k, v in value.items()}
        return value

    trimmed = shrink(payload)
    trimmed["truncated_response"] = True
    trimmed["hint_truncated"] = "The response was trimmed to fit the size budget; narrow the request (limits, filters, fields) for full detail."
    return trimmed


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

    max_chars = ctx.config.tools.max_response_chars

    async def tool(**kwargs: Any) -> dict[str, Any]:
        payload = {k: _plain(v) for k, v in kwargs.items()}
        result = await _guarded(spec.name, lambda: spec.run(ctx, payload))
        return _trim_payload(result, max_chars)

    tool.__name__ = spec.name
    tool.__qualname__ = spec.name
    tool.__doc__ = spec.description
    tool.__signature__ = inspect.Signature(parameters, return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    return tool


def simplify_schema(schema: dict) -> dict:
    """Flatten a JSON Schema for the widest client compatibility.

    Inlines ``$ref`` targets, collapses ``anyOf``/``oneOf`` that only add
    ``null`` (optional parameters are expressed by their default instead),
    and drops ``title`` keys. The result is a plain subset understood by
    Claude, Gemini, and OpenAI tool-calling alike.
    """
    defs = schema.get("$defs") or {}

    def resolve(node: Any, depth: int = 0) -> Any:
        if isinstance(node, list):
            return [resolve(n, depth) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node and depth < 32:
            target = defs.get(str(node["$ref"]).split("/")[-1], {})
            siblings = {k: v for k, v in node.items() if k != "$ref"}
            return {**resolve(target, depth + 1), **resolve(siblings, depth + 1)}

        out: dict = {}
        union: Optional[list] = None
        for key, value in node.items():
            if key in ("title", "$defs"):
                continue
            if key in ("anyOf", "oneOf"):
                options = [o for o in value if not (isinstance(o, dict) and o.get("type") == "null")]
                if len(options) == 1:
                    union = options
                else:
                    out[key] = resolve(options, depth + 1)
                continue
            out[key] = resolve(value, depth + 1)
        if union is not None:
            inner = resolve(union[0], depth + 1)
            out = {**inner, **out}
        return out

    return resolve(schema)


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
    stateful = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False,
    )
    writes = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False,
    )
    destructive = ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False,
    )

    for spec in enabled_specs(config):
        if spec.writes:
            annotations = destructive if spec.name.split("_")[1] == "delete" else writes
        elif spec.stateful:
            annotations = stateful
        else:
            annotations = read_only
        mcp.add_tool(
            _make_tool_function(spec, ctx),
            name=spec.name,
            title=spec.title,
            description=spec.description,
            annotations=annotations,
        )
        registered = mcp._tool_manager.get_tool(spec.name)  # noqa: SLF001 - schema post-processing hook
        if registered is not None:
            registered.parameters = simplify_schema(registered.parameters)

    return mcp


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def transport_security_for(config: Config) -> Optional[TransportSecuritySettings]:
    """DNS-rebinding protection for the HTTP transports.

    On a loopback bind, only Host headers naming localhost are accepted so a
    malicious web page cannot reach the server through the browser. On any
    other bind the deployment is expected to sit behind an authenticating
    proxy (or use ``server.http_bearer_token``), so the SDK default is kept.
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


class BearerAuthMiddleware:
    """ASGI middleware requiring ``Authorization: Bearer <token>`` on HTTP requests."""

    def __init__(self, app: Any, token: str, exempt_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()
        self.exempt_paths = set(exempt_paths)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        provided = b""
        for key, value in scope.get("headers") or []:
            if key.lower() == b"authorization":
                provided = value
                break
        if not hmac.compare_digest(provided, self._expected):
            from starlette.responses import JSONResponse

            response = JSONResponse(
                {"error": "unauthorized", "detail": "A valid bearer token is required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="qlik-cloud-mcp-server"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_http_app(mcp: MCPServer, config: Config) -> Any:
    """The Streamable HTTP ASGI app with a health endpoint and optional bearer auth."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "qlik-cloud-mcp-server", "version": __version__})

    app: Any = mcp.streamable_http_app(
        streamable_http_path=config.server.http_path,
        stateless_http=config.server.http_stateless,
        transport_security=transport_security_for(config),
        host=config.server.http_host,
    )
    if config.server.http_bearer_token:
        app = BearerAuthMiddleware(app, config.server.http_bearer_token, exempt_paths=("/healthz",))
    return app


def run_server(config: Config) -> None:
    """Run the MCP server on the configured transport (blocking)."""
    mcp = create_server(config)
    transport = config.server.transport

    if transport == "stdio":
        logger.info("Starting Qlik Cloud MCP Server (stdio), tenant: %s", config.tenant_host)
        mcp.run("stdio")
    elif transport == "streamable-http":
        import uvicorn

        if not config.server.http_bearer_token and config.server.http_host not in _LOOPBACK_HOSTS:
            logger.warning(
                "HTTP transport is bound to %s without server.http_bearer_token; "
                "anyone who can reach it can use your Qlik credentials.",
                config.server.http_host,
            )
        logger.info(
            "Starting Qlik Cloud MCP Server (Streamable HTTP) on http://%s:%d%s, tenant: %s, auth: %s",
            config.server.http_host, config.server.http_port, config.server.http_path,
            config.tenant_host, "bearer token" if config.server.http_bearer_token else "none",
        )
        uvicorn.run(
            build_http_app(mcp, config),
            host=config.server.http_host,
            port=config.server.http_port,
            log_level=config.server.log_level.lower(),
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
