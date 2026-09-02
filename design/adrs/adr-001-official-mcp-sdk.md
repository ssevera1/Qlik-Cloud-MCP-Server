# ADR-001: Official MCP SDK over Custom JSON-RPC Implementation

**Status:** Accepted
**Date:** 2026-02-21

## Context

The MCP Server must implement the Model Context Protocol, a JSON-RPC 2.0 based protocol with specific message types for tool listing, invocation, and resource management. We can either:

1. Use the official `mcp` Python SDK maintained by Anthropic
2. Build a custom JSON-RPC implementation from scratch

## Decision

**Chosen: Official `mcp` Python SDK.**

## Rationale

1. **Protocol compliance**: The SDK guarantees conformance to the MCP specification, including edge cases in schema validation, error codes, and transport negotiation that a custom implementation would need to independently discover and handle.

2. **Transport abstraction**: The SDK provides stdio, Streamable HTTP, and legacy SSE transports out of the box. Building custom transport handling (session management, resumability) would be a significant engineering effort.

3. **Tool schema generation**: The SDK auto-generates JSON Schema for tool inputs from Python function signatures and Pydantic models, eliminating manual schema maintenance.

4. **Upstream maintenance**: As the MCP spec evolves, the SDK tracks changes. A custom implementation would require manual spec monitoring and updating.

### Trade-offs accepted:

- **Dependency**: Adds a runtime dependency on the `mcp` package. Acceptable since this is the core protocol of the server.
- **Abstraction leakage**: If the SDK has bugs or limitations, we depend on upstream fixes. Mitigated by the SDK being actively maintained.

## Consequences

- `mcp>=2.0.0` is a core dependency (SDK v2, September 2026 update)
- Tools are registered on the high-level `MCPServer` via `add_tool()` with flat, typed signatures; the Pydantic input models in `tools/` remain the source of truth for descriptions and constraints
- Transport selection is delegated to `MCPServer.run()`: stdio (default), streamable-http, or sse
- Tool results are returned as JSON objects, which the SDK emits as both text and structured content

## Update 2026-09-02

The SDK's v2 release renamed `FastMCP` to `MCPServer`, removed the decorator API of the low-level `Server`, and made Streamable HTTP the primary HTTP transport. The server was ported accordingly; the decision itself stands.
