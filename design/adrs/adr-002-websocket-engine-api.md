# ADR-002: WebSocket Engine API over REST-Only for Hypercube Data

**Status:** Accepted
**Date:** 2026-02-21

## Context

Qlik Cloud offers two API surfaces:

1. **REST API** (`/api/v1/*`): Standard HTTP for app management, catalog, metadata
2. **Engine API** (`wss://tenant/app/{id}`): WebSocket JSON-RPC for the Associative Engine: hypercubes, selections, calculations, sheet manipulation

The `qlik_get_hypercube_data` tool needs to retrieve computed/aggregated data from Qlik apps. This data lives in the Associative Engine, not in static REST endpoints. The same applies to field discovery (`qlik_get_fields`) and sheet inspection.

## Decision

**Chosen: WebSocket Engine API as the primary data retrieval mechanism**, with REST API used for catalog/search operations.

## Rationale

1. **Hypercubes require the engine**: There is no REST endpoint that computes aggregated hypercube data. The Engine API is the only way to define dimensions + measures and get computed results back. This is a technical requirement, not a preference.

2. **Section Access enforcement**: The Engine API respects Section Access (row-level security) natively. When we request a hypercube, the engine applies all security rules before returning data. REST metadata endpoints do not provide this level of data governance.

3. **Selections and filtering**: The Engine API supports `SelectValues` and `ClearAll`, allowing the MCP tool to apply filters before fetching data. REST APIs cannot replicate this associative selection model.

4. **Sheet creation**: The `qlik_create_sheet` tool requires Engine API methods (`CreateObject`, `CreateChild`, `SetProperties`, `DoSave`) that have no REST equivalent. In Qlik Cloud, objects created through the engine are only persisted after `DoSave`.

### Trade-offs accepted:

- **Connection complexity**: WebSocket connections require careful lifecycle management (open, operate, close). More complex than simple HTTP requests.
- **Latency**: WebSocket handshake adds initial latency per connection. Mitigated by performing all operations within a single connection per tool call.
- **State management**: The Engine API is inherently stateful (session-based). Our stateless tool design requires opening and closing connections per call, which is less efficient than persistent sessions but simpler to manage.

## Consequences

- `websockets` is a core dependency
- The `EngineClient` implements JSON-RPC 2.0 over WebSocket
- Each tool call opens a fresh WebSocket connection (stateless design)
- Error handling must account for WebSocket-specific failures (connection drops, timeouts, id-less engine notifications interleaved with responses)
- Large hypercubes are paged with `GetHyperCubeData` up to the configured row limit
- REST API is used only for `qlik_search` (catalog queries) and OAuth token acquisition
