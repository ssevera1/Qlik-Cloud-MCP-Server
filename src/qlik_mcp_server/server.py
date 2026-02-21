"""MCP Server definition and tool registration.

This is the core server that implements the Model Context Protocol,
registering all Qlik Cloud tools and handling their invocation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .auth import AuthManager
from .config import Config
from .engine_client import EngineClient
from .qlik_cloud_client import QlikCloudClient
from .tools.create_sheet import (
    TOOL_DESCRIPTION as CREATE_SHEET_DESC,
    CreateSheetInput,
    handle_create_sheet,
)
from .tools.get_hypercube_data import (
    TOOL_DESCRIPTION as HYPERCUBE_DESC,
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


def create_server(config: Config) -> Server:
    """Create and configure the MCP server with all Qlik tools."""

    server = Server("qlik-cloud-mcp-server")
    auth = AuthManager(config)
    qlik_client = QlikCloudClient(config, auth)
    engine_client = EngineClient(config, auth)

    # ── Tool Listing ──────────────────────────────────────────────
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Return the list of available Qlik Cloud tools."""
        tools: list[Tool] = []

        if config.tools.get_sheet_details:
            tools.append(Tool(
                name="qlik_get_sheet_details",
                description=SHEET_DETAILS_DESC,
                inputSchema=GetSheetDetailsInput.model_json_schema(),
            ))

        if config.tools.get_hypercube_data:
            tools.append(Tool(
                name="qlik_get_hypercube_data",
                description=HYPERCUBE_DESC,
                inputSchema=GetHypercubeDataInput.model_json_schema(),
            ))

        if config.tools.create_sheet:
            tools.append(Tool(
                name="qlik_create_sheet",
                description=CREATE_SHEET_DESC,
                inputSchema=CreateSheetInput.model_json_schema(),
            ))

        if config.tools.search:
            tools.append(Tool(
                name="qlik_search",
                description=SEARCH_DESC,
                inputSchema=SearchInput.model_json_schema(),
            ))

        logger.info("Listed %d tools", len(tools))
        return tools

    # ── Tool Invocation ───────────────────────────────────────────
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Handle a tool invocation from an AI agent."""
        logger.info("Tool called: %s", name)
        logger.debug("Arguments: %s", json.dumps(arguments, default=str))

        try:
            result = await _dispatch_tool(
                name, arguments, config, qlik_client, engine_client
            )

            result_text = json.dumps(result, indent=2, default=str)
            logger.debug("Result: %s", result_text[:500])

            return [TextContent(type="text", text=result_text)]

        except Exception as e:
            logger.error("Tool %s failed: %s", name, e, exc_info=True)
            error_result = json.dumps({
                "error": str(e),
                "tool": name,
                "hint": "Check the server logs for details.",
            })
            return [TextContent(type="text", text=error_result)]

    return server


async def _dispatch_tool(
    name: str,
    arguments: dict,
    config: Config,
    qlik_client: QlikCloudClient,
    engine_client: EngineClient,
) -> dict:
    """Route a tool call to the appropriate handler."""

    if name == "qlik_get_sheet_details":
        return await handle_get_sheet_details(engine_client, arguments)

    elif name == "qlik_get_hypercube_data":
        return await handle_get_hypercube_data(
            engine_client, arguments,
            max_rows_limit=config.tools.max_hypercube_rows,
        )

    elif name == "qlik_create_sheet":
        return await handle_create_sheet(
            engine_client, arguments,
            sheet_prefix=config.tools.created_sheet_prefix,
            allow_creation=config.tools.allow_sheet_creation,
        )

    elif name == "qlik_search":
        return await handle_search(qlik_client, arguments)

    else:
        return {"error": f"Unknown tool: {name}"}


async def run_stdio_server(config: Config) -> None:
    """Run the MCP server with stdio transport."""
    server = create_server(config)
    logger.info(
        "Starting Qlik Cloud MCP Server (stdio) — tenant: %s",
        config.tenant_host,
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            server.create_initialization_options(),
        )


async def run_sse_server(config: Config) -> None:
    """Run the MCP server with SSE transport."""
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route

    server = create_server(config)
    sse = SseServerTransport("/messages")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0], streams[1],
                server.create_initialization_options(),
            )

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages", endpoint=sse.handle_post_message, methods=["POST"]),
        ],
    )

    import uvicorn
    logger.info(
        "Starting Qlik Cloud MCP Server (SSE) on %s:%d — tenant: %s",
        config.server.sse_host, config.server.sse_port, config.tenant_host,
    )

    uvicorn_config = uvicorn.Config(
        app,
        host=config.server.sse_host,
        port=config.server.sse_port,
        log_level=config.server.log_level.lower(),
    )
    server_instance = uvicorn.Server(uvicorn_config)
    await server_instance.serve()
