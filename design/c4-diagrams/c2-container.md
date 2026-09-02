# C2: Container Diagram

Runtime containers that make up the MCP Server.

```mermaid
C4Container
    title Container Diagram: Qlik Cloud MCP Server

    Person(aiAgent, "AI Agent", "Calls MCP tools")

    System_Boundary(server, "Qlik Cloud MCP Server") {
        Container(mcpRuntime, "MCP Runtime", "Python / mcp SDK v2 (MCPServer)", "Handles MCP protocol:<br/>tool listing, invocation,<br/>input validation, structured results")

        Container(toolRegistry, "Tool Registry", "Python (tools/registry.py, server.py)", "16 ToolSpecs registered<br/>with flat typed signatures;<br/>sanitizes errors")

        Container(restClient, "REST API Client", "Python / httpx", "Qlik Cloud REST API:<br/>catalog search,<br/>app metadata, spaces")

        Container(engineClient, "Engine API Client", "Python / websockets", "WebSocket JSON-RPC:<br/>fields, hypercubes, sheets,<br/>selections, save")

        Container(authModule, "Auth Module", "Python", "API key injection +<br/>OAuth2 M2M token<br/>acquisition & refresh")

        Container(configMgr, "Config Manager", "Python / pyyaml", "Loads config.yaml or env,<br/>resolves env vars,<br/>validates settings")
    }

    System_Ext(qlikRest, "Qlik Cloud REST API", "/api/v1/items, /api/v1/apps, /oauth/token")
    System_Ext(qlikEngine, "Qlik Associative Engine", "wss://tenant/app/{id}")

    Rel(aiAgent, mcpRuntime, "MCP Protocol", "stdio / Streamable HTTP")
    Rel(mcpRuntime, toolRegistry, "Dispatches tool calls")
    Rel(toolRegistry, restClient, "Search")
    Rel(toolRegistry, engineClient, "Fields, hypercubes, sheets")
    Rel(restClient, authModule, "Gets auth headers")
    Rel(engineClient, authModule, "Gets auth headers")
    Rel(authModule, configMgr, "Reads credentials")
    Rel(restClient, qlikRest, "HTTPS")
    Rel(engineClient, qlikEngine, "WebSocket")
```

## Container Responsibilities

### MCP Runtime
- Implements the Model Context Protocol server using the official `mcp` SDK v2 `MCPServer`
- Handles tool listing (`tools/list`), tool invocation (`tools/call`), and error responses
- Supports stdio transport (for local agents like Claude Code) and Streamable HTTP (for remote agents); legacy SSE is still available with a deprecation warning
- Validates tool inputs against the generated JSON Schema before dispatching
- Emits every tool result as both text and structured content

### Tool Registry
- `tools/registry.py` lists 16 `ToolSpec`s in workflow order: discover (`qlik_search`, `qlik_describe_app`), data model (`qlik_get_fields`, `qlik_get_field_values`, `qlik_search_field_values`, `qlik_list_dimensions`, `qlik_list_measures`, `qlik_list_bookmarks`), dashboards (`qlik_list_sheets`, `qlik_get_sheet_details`, `qlik_get_chart_info`, `qlik_get_chart_data`), compute (`qlik_get_hypercube_data`), build (`qlik_create_sheet`, `qlik_add_chart`, `qlik_add_filter`)
- `server.py` generates each tool's signature from its Pydantic input model, so descriptions and constraints live in one place
- Marks read-only tools with the MCP `readOnlyHint` annotation; the three build tools are the only writers and are not registered when `allow_sheet_creation` is false
- Honors `tools.disabled_tools` and the legacy per-tool booleans
- Converts every failure into a JSON error payload; unexpected exceptions are logged in full but reported to the agent generically

### REST API Client
- Async HTTP client (httpx) for the Qlik Cloud REST API
- Catalog search (`GET /api/v1/items`, page size capped at 100)
- App metadata (`/api/v1/apps/{id}`), data model metadata (`/api/v1/apps/{id}/data/metadata`), and space listing
- Retries timeouts, honors `Retry-After` on 429 (capped at 60 seconds), and maps transport failures to a safe error

### Engine API Client
- WebSocket client for the Qlik Associative Engine JSON-RPC protocol
- Opens one connection per tool call (stateless) and closes it afterwards
- Implements the "handle" system: Global (-1) to Doc via `OpenDoc`, then GenericObject handles
- Methods used: `OpenDoc`, `GetAppLayout`, `GetObjects`, `GetObject`, `GetLayout`, `GetProperties`, `CreateSessionObject` (field list, list object, master item lists, bookmark list, hypercube), `SearchListObjectFor`, `SearchResults`, `GetHyperCubeData`, `GetListObjectData`, `ApplyBookmark`, `GetField`, `SelectValues`, `CreateObject`, `CreateChild`, `SetProperties`, `DoSave`
- Unwraps raw results (`qLayout`, `qDataPages`, `qResult`, `qList`, `qProp`), which the JSON-RPC engine wraps by parameter name
- Skips engine notifications while waiting for a response and enforces per-message and per-request deadlines

### Auth Module
- **API Key mode**: injects `Authorization: Bearer {key}`
- **OAuth2 M2M mode**: posts a JSON client-credentials request to `/oauth/token`, caches the token, and refreshes before expiry
- Refuses token URLs that are not on the configured tenant host
- Provides auth headers for both REST (HTTP) and Engine (WebSocket) clients

### Config Manager
- Loads `config.yaml` with `${ENV_VAR}` interpolation, or builds config from environment variables alone
- Validates the tenant URL is a bare https origin, credentials are present, the transport is known, and the port is an integer
- Accepts legacy `sse_host` / `sse_port` keys and maps them to `http_host` / `http_port`

## Transport Modes

```
+-----------------------------+      +------------------------------+
|  STDIO Transport (Default)  |      |  Streamable HTTP (Remote)    |
|                             |      |                              |
|  Claude Code <-> stdin/out  |      |  Agent <-> HTTP /mcp         |
|  Local process, no network  |      |  Network, port 8080          |
|  Ideal for dev & testing    |      |  Behind an auth proxy        |
+-----------------------------+      +------------------------------+
```

When the HTTP transport is bound to a loopback address the server enables the SDK's DNS-rebinding protection (only localhost Host headers and origins are accepted). On any other bind address host validation is left to the reverse proxy in front of the server.
