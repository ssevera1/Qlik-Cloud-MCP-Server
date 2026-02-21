# C4: Code Diagram

Class-level detail showing key relationships.

```mermaid
classDiagram
    class Config {
        +tenant_url: str
        +api_key: str
        +oauth: OAuthConfig
        +transport: str
        +tool_settings: ToolSettings
        +load(path: str) Config$
        +validate() list~str~
    }

    class OAuthConfig {
        +client_id: str
        +client_secret: str
        +token_url: str
    }

    class ToolSettings {
        +get_sheet_details: bool
        +get_hypercube_data: bool
        +create_sheet: bool
        +search: bool
        +max_hypercube_rows: int
        +max_hypercube_columns: int
        +allow_sheet_creation: bool
        +created_sheet_prefix: str
    }

    class AuthManager {
        -config: Config
        -_access_token: str
        -_token_expiry: datetime
        +get_rest_headers() dict
        +get_ws_headers() dict
        -_refresh_oauth_token() str
    }

    class QlikCloudClient {
        -config: Config
        -auth: AuthManager
        -_client: httpx.AsyncClient
        +search_items(query, resource_type, space, limit) list~dict~
        +get_app(app_id) dict
        +list_apps(space_id) list~dict~
        +get_spaces() list~dict~
        +close()
    }

    class EngineClient {
        -config: Config
        -auth: AuthManager
        +open_app(app_id) EngineSession
    }

    class EngineSession {
        -ws: WebSocket
        -doc_handle: int
        -_request_id: int
        +get_sheets() list~dict~
        +get_sheet_layout(sheet_id) dict
        +get_object_layout(object_id) dict
        +create_hypercube(dimensions, measures, page_size) HypercubeResult
        +apply_selections(field, values) bool
        +clear_selections()
        +create_sheet(title, objects) dict
        +close()
        -_send(method, handle, params) dict
        -_next_id() int
    }

    class HypercubeResult {
        +headers: list~str~
        +rows: list~list~
        +total_rows: int
        +truncated: bool
        +to_table() str
        +to_records() list~dict~
    }

    class MCPServer {
        -config: Config
        -qlik_client: QlikCloudClient
        -engine_client: EngineClient
        +run(transport) void
        -_register_tools()
        -_handle_get_sheet_details(params) ToolResult
        -_handle_get_hypercube_data(params) ToolResult
        -_handle_create_sheet(params) ToolResult
        -_handle_search(params) ToolResult
    }

    Config --* OAuthConfig
    Config --* ToolSettings
    AuthManager --> Config
    QlikCloudClient --> Config
    QlikCloudClient --> AuthManager
    EngineClient --> Config
    EngineClient --> AuthManager
    EngineClient ..> EngineSession : creates
    EngineSession ..> HypercubeResult : returns
    MCPServer --> Config
    MCPServer --> QlikCloudClient
    MCPServer --> EngineClient
```

## Key Design Patterns

### Connection-per-Call (Engine API)
Each tool invocation opens a fresh WebSocket connection, performs the operation, and closes. This keeps the MCP server stateless and avoids session management complexity.

```python
async def _handle_get_hypercube_data(self, params):
    async with self.engine_client.open_app(params["app_id"]) as session:
        result = await session.create_hypercube(
            dimensions=params["dimensions"],
            measures=params["measures"],
        )
    return result.to_records()
```

### Async Context Manager for Engine Sessions
`EngineSession` implements `__aenter__`/`__aexit__` to ensure WebSocket connections are always properly closed, even on errors.

### Pydantic Input Validation
Tool input parameters are validated using Pydantic models before being passed to handlers, providing clear error messages when agents send malformed requests.

### MCP SDK Integration
The `mcp` SDK handles protocol serialization, transport negotiation, and tool schema exposure. The server only implements the tool handler functions.
