# ADR-004: Stateless Tool Calls over Persistent Engine Sessions

**Status:** Accepted
**Date:** 2026-02-21

## Context

The Qlik Engine API is session-based: you open a WebSocket connection, get a document handle, and all subsequent operations use that handle. Sessions maintain state (current selections, active objects).

Two approaches for the MCP server:

1. **Persistent sessions**: Keep WebSocket connections open across multiple tool calls. Reuse sessions for the same app.
2. **Stateless (connection-per-call)**: Open a fresh WebSocket for each tool invocation, close it when done.

## Decision

**Chosen: Stateless tool calls, one WebSocket connection per tool invocation.**

## Rationale

1. **MCP protocol alignment**: MCP tools are designed to be stateless function calls. Each invocation should produce the same result regardless of previous calls. Persistent sessions would leak state (selections from a previous call affecting the next).

2. **Simplicity**: No session pool management, no connection health monitoring, no stale session handling. The server is a pure function: input, Qlik call, output.

3. **Concurrency safety**: Multiple AI agents (or the same agent making parallel calls) won't interfere with each other's sessions. Each call gets its own isolated engine session.

4. **Resilience**: If a WebSocket drops, only that one tool call fails. No cascading failures from a shared session pool.

### Trade-offs accepted:

- **Latency overhead**: Each call incurs WebSocket handshake + TLS + app open latency (~200-500ms). For a persistent session this cost is paid once.
- **Engine load**: More connection churn on the Qlik Engine. Acceptable for the expected call volume (10s-100s/hour, not 1000s/second).
- **No selection carryover**: If an agent wants to make a selection and then query, it must specify both in a single tool call. Cannot "select Region=East" in one call and "get Revenue" in the next.

## Consequences

- `EngineClient.open_app()` is an async context manager that always opens and closes the session cleanly
- Tool inputs must be self-contained (filters + query in one call)
- No session pool or connection cache in the server
- The `qlik_get_hypercube_data` tool accepts an optional `filters` parameter for selections within a single call, and reports any filter the engine could not match in `filters_not_matched`
- `qlik_create_sheet` saves the app (`DoSave`) inside the same call, since nothing survives the session otherwise

## Update 2026-09-02: sessions are reused, calls stay self-contained

Version 0.4.0 keeps one Engine WebSocket per app open between calls (`qlik.reuse_sessions`, see ADR-006) because the connect and `OpenDoc` round trips were most of each call's latency. The property this ADR protects, that a data call is self-contained, is kept differently: per-call `filters` on `qlik_create_data_object` are applied in a temporary alternate state that is removed afterwards, and temporary session objects are destroyed after every call. What changed is that the server now also offers explicit session state on purpose: `qlik_select_values`, `qlik_clear_selections`, `qlik_get_current_selections`, and `qlik_select_bookmark` act on the app's live session, the way Qlik's own hosted MCP server works, so an agent can explore interactively. Data tools report that state so it is never invisible. Setting `qlik.reuse_sessions: false` restores one connection per call.
