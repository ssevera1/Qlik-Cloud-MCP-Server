# C4: Code Diagram

Class-level detail showing key relationships.

```mermaid
classDiagram
    class Config {
        +qlik: QlikConfig
        +server: ServerConfig
        +tools: ToolSettings
        +load(path) Config$
        +from_env() Config$
        +validate() list~str~
        +auth_mode: str
        +tenant_host: str
    }

    class QlikConfig {
        +tenant_url: str
        +api_key: str
        +oauth: OAuthConfig
        +default_app_id: str
        +timeout_seconds: int
        +max_retries: int
    }

    class OAuthConfig {
        +client_id: str
        +client_secret: str
        +token_url: str
    }

    class ServerConfig {
        +transport: str
        +http_host: str
        +http_port: int
        +http_path: str
        +log_level: str
    }

    class ToolSettings {
        +search: bool
        +get_fields: bool
        +get_sheet_details: bool
        +get_hypercube_data: bool
        +create_sheet: bool
        +max_hypercube_rows: int
        +max_hypercube_columns: int
        +allow_sheet_creation: bool
        +created_sheet_prefix: str
    }

    class AuthManager {
        -config: Config
        -_transport: httpx transport (tests)
        -_access_token: str
        -_token_expiry: float
        +get_rest_headers() dict
        +get_ws_headers() dict
        -_token_url() str
        -_refresh_oauth_token() str
    }

    class QlikCloudClient {
        -config: Config
        -auth: AuthManager
        -_client: httpx.AsyncClient
        +search_items(query, resource_type, space_id, limit) list~dict~
        +get_app(app_id) dict
        +list_apps(space_id, limit) list~dict~
        +get_spaces() list~dict~
        +close()
        -_request(method, path, params, json_data)
    }

    class EngineClient {
        -config: Config
        -auth: AuthManager
        +open_app(app_id) EngineSession
    }

    class EngineSession {
        -_ws: WebSocket
        -_doc_handle: int
        -_request_id: int
        +get_sheets() list~dict~
        +get_sheet_layout(sheet_id) dict
        +get_object_layout(object_id) dict
        +describe_sheet(layout) dict$
        +get_fields() list~dict~
        +create_hypercube(dimensions, measures, page_size, max_rows) HypercubeResult
        +apply_selections(field, values) bool
        +clear_selections()
        +create_sheet(title, description, objects) dict
        +close()
        -_send(method, handle, params)
        -_layout_cells(created) list~dict~$
        -_build_child_props(obj_def) dict$
    }

    class HypercubeResult {
        +headers: list~str~
        +rows: list~list~
        +total_rows: int
        +truncated: bool
        +to_table() str
        +to_records() list~dict~
    }

    class server_py {
        +TOOL_NAMES
        +create_server(config, qlik_client?, engine_client?) MCPServer
        +run_server(config)
        +transport_security_for(config)
        -_guarded(tool_name, call) dict
    }

    class MCPServer {
        <<mcp SDK v2>>
        +add_tool(fn, name, description, annotations)
        +list_tools()
        +call_tool(name, arguments)
        +run(transport, ...)
    }

    Config --* QlikConfig
    Config --* ServerConfig
    Config --* ToolSettings
    QlikConfig --* OAuthConfig
    AuthManager --> Config
    QlikCloudClient --> Config
    QlikCloudClient --> AuthManager
    EngineClient --> Config
    EngineClient --> AuthManager
    EngineClient ..> EngineSession : creates
    EngineSession ..> HypercubeResult : returns
    server_py --> Config
    server_py --> QlikCloudClient
    server_py --> EngineClient
    server_py ..> MCPServer : builds
```

## Key Design Patterns

### Connection-per-Call (Engine API)
Each tool invocation opens a fresh WebSocket connection, performs the operation, and closes. This keeps the MCP server stateless and avoids session management complexity.

```python
async def handle_get_hypercube_data(engine, params, max_rows_limit=10000, max_columns_limit=50):
    input_data = GetHypercubeDataInput(**params)
    async with engine.open_app(input_data.app_id) as session:
        for f in input_data.filters or []:
            await session.apply_selections(f.field, f.values)
        result = await session.create_hypercube(
            dimensions=input_data.dimensions,
            measures=input_data.measures,
            max_rows=min(input_data.max_rows or 1000, max_rows_limit),
        )
    return {"headers": result.headers, "data": result.rows, ...}
```

### Async Context Manager for Engine Sessions
`EngineClient.open_app()` is an `asynccontextmanager`: it validates the app id, connects, calls `OpenDoc`, yields an `EngineSession`, and always closes the socket, even on errors.

### Handlers take dicts, the SDK sees flat signatures
Each tool module exposes a `handle_*` coroutine that accepts a plain dict and returns a plain dict. `server.py` wraps each handler in a function with flat, typed parameters whose descriptions and constraints are copied from the Pydantic input model, then registers it with `MCPServer.add_tool()`. The Pydantic model stays the single source of truth; the SDK generates the JSON Schema from the wrapper signature.

### Pydantic Input Validation
Tool inputs are validated twice: by the SDK against the generated schema, and again by the Pydantic model inside the handler (which also runs custom validators such as the allowed resource types). Validation failures become `{"error": "Invalid input: ..."}` payloads.

### Error Sanitization
`_guarded()` in `server.py` maps failures to agent-readable payloads. Engine and REST errors keep their message (bounded to 500 characters). Authentication and unexpected errors are logged in full but reported generically so credentials and stack details never reach the model.

### MCP SDK Integration
The `mcp` SDK handles protocol serialization, transport negotiation, structured output, and schema exposure. The server only implements the tool functions and picks the transport in `run_server()`.
