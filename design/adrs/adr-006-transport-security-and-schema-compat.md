# ADR-006: Session Reuse, Client-Neutral Schemas, and Authenticated HTTP

**Status:** Accepted
**Date:** 2026-09-02

## Context

Version 0.4.0 turned the server from an analytics-only tool set into a full Qlik Cloud surface (107 tools) meant to serve two very different clients: Claude Code on a developer's machine over stdio, and Gemini Enterprise reaching a hosted endpoint over Streamable HTTP. Three problems followed.

1. **Latency.** Every engine tool opened a WebSocket, called `OpenDoc`, worked, and closed. Against Qlik Cloud that handshake costs roughly half a second per call, which dominates a typical agent loop of ten to thirty calls per question.
2. **Schema compatibility.** Pydantic emits `$defs`, `$ref`, `anyOf` unions for optional fields, and `title` keys. Claude accepts these; Gemini's function-declaration subset rejects `$ref` and unions, so a tool list that loads in Claude Code fails to register in Gemini Enterprise.
3. **Exposure.** The HTTP transport carried no authentication. That is acceptable on localhost behind Claude Code, and unacceptable for a hosted endpoint that holds a service-account credential for the whole tenant.

## Decision

### Keep one engine session per app between calls

`EngineClient` pools one WebSocket per app (`qlik.reuse_sessions`, default on). Calls to the same app are serialized on that socket; different apps run in parallel. Sessions close after `qlik.session_idle_seconds` (default 300) of inactivity, the pool is capped at `qlik.max_sessions`, and a socket that fails mid-call is evicted rather than reused. Temporary session objects are destroyed at the end of each call so long-lived sessions do not accumulate them in the engine.

Selections made with `qlik_select_values` and `qlik_select_bookmark` deliberately persist on the session, matching how Qlik's own hosted MCP server behaves and what a user working in the app would expect. Per-call `filters` on `qlik_create_data_object` are applied in a temporary alternate state that is removed afterwards, so a one-off filtered query never changes what the next call sees. This keeps ADR-004's "self-contained call" property for the data tools while allowing interactive exploration.

### Simplify every tool schema after registration

After the SDK generates a tool's JSON Schema, `simplify_schema` inlines `$defs`, collapses `anyOf [T, null]` to `T` (optionality is already expressed by the default), and drops `title` keys. The result is the subset understood by Claude, Gemini, and OpenAI tool calling alike; a contract test asserts no registered schema contains `$ref`, `$defs`, `anyOf`, `oneOf`, `allOf`, or `title`.

### Require a bearer token on HTTP when configured

`server.http_bearer_token` (or `QLIK_MCP_HTTP_BEARER_TOKEN`) makes every MCP request require `Authorization: Bearer <token>`, compared in constant time; `/healthz` stays open for load balancers. `server.http_stateless` enables the stateless Streamable HTTP mode Gemini Enterprise and load-balanced deployments prefer. When the transport binds beyond localhost without a token, the server logs a warning at startup.

### Let deployments size the tool list

The full catalog costs about 23k tokens of tool definitions per session. `tools.profile` offers `full` (default), `analytics` (the app-analysis groups only, about 30 tools), and `readonly`; `tools.disabled_groups` and `tools.disabled_tools` trim further. `tools.allow_writes` hides every tool that changes Qlik Cloud content.

## Consequences

- A tool call on a warm session skips connect and OpenDoc; only the first call to an app pays for them.
- Session state is per server process. Two agents sharing one HTTP server and the same app share that app's selections; deployments that need isolation run one server per agent or keep `qlik.reuse_sessions: false`.
- `qlik_create_data_object` returns `columns` and `rows` (or `table` / `csv` on request) instead of duplicating data as text; results are trimmed at `tools.max_response_chars`.
- Tool annotations distinguish read-only, session-state (selections, bookmark apply), write, and destructive (delete) tools so clients can apply their own approval policies.
