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
        +reuse_sessions: bool
        +session_idle_seconds: int
        +max_sessions: int
        +cache_ttl_seconds: int
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
        +http_bearer_token: str
        +http_stateless: bool
        +log_level: str
    }

    class ToolSettings {
        +profile: str
        +allow_writes: bool
        +max_response_chars: int
        +disabled_tools: list~str~
        +search: bool
        +get_fields: bool
        +get_sheet_details: bool
        +get_hypercube_data: bool
        +create_sheet: bool
        +is_enabled(tool_name) bool
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
        +call(method, path, params, json, text, cache)
        +fetch_text_url(url, max_chars) str
        +search_items(query, resource_type, space_id, limit) list~dict~
        +get_app(app_id) dict
        +get_app_data_metadata(app_id) dict
        +list_apps(space_id, limit) list~dict~
        +get_spaces() list~dict~
        +close()
        -_request(method, path, params, json_data)
    }

    class EngineClient {
        -config: Config
        -auth: AuthManager
        -_pool: dict~str, _PooledSession~
        +open_app(app_id) EngineSession
        +close()
        -_acquire(app_id) _PooledSession
        -_evict_idle()
    }

    class EngineSession {
        -_ws: WebSocket
        -_doc_handle: int
        -_request_id: int
        +get_app_layout() dict
        +get_script() str
        +cleanup_temp()
        +select_values(field, values, match, toggle) dict
        +clear_selections(fields) dict
        +get_current_selections() list~dict~
        +create_bookmark(title, description, sheet_id) dict
        +delete_bookmark(id) bool
        +create_dimension(...) dict
        +update_dimension(...) dict
        +delete_dimension(id) bool
        +create_measure(...) dict
        +update_measure(...) dict
        +delete_measure(id) bool
        +list_sheets() list~dict~
        +get_sheets() list~dict~
        +get_sheet_layout(sheet_id) dict
        +get_object_layout(object_id) dict
        +describe_sheet(layout) dict$
        +get_fields() list~dict~
        +get_field_values(field, max_values, match) dict
        +search_field_values(terms, fields, max_matches) dict
        +get_master_items() dict
        +get_bookmarks() list~dict~
        +apply_bookmark(id) bool
        +get_object_info(object_id) dict
        +get_object_data(object_id, max_rows) HypercubeResult
        +create_hypercube(dimensions, measures, page_size, max_rows, filters, sort_by, sort_descending) HypercubeResult
        +apply_selections(field, values) bool
        +clear_selections()
        +create_sheet(title, description, objects) dict
        +add_objects_to_sheet(sheet_id, objects) dict
        +add_filter_pane(sheet_id, fields, title) dict
        +close()
        -_send(method, handle, params)
        -_read_hypercube(hc, handle, max_rows, page_size) HypercubeResult
        -_layout_cells(created) list~dict~$
        -_append_cells(existing, created) list~dict~$
        -_build_child_props(obj_def) dict$
    }

    class HypercubeResult {
        +headers: list~str~
        +rows: list~list~
        +total_rows: int
        +truncated: bool
        +to_table() str
        +to_markdown() str
        +to_csv() str
        +as_payload(fmt) dict
    }

    class RestTool {
        +name, title, description
        +method: str
        +path: str
        +params: tuple~P~
        +group: str
        +writes: bool
        +body / query / result / custom
    }

    class ToolSpec {
        +name: str
        +title: str
        +description: str
        +input_model: type~BaseModel~
        +run(ctx, params) dict
        +writes: bool
    }

    class registry_py {
        +ENGINE_TOOL_SPECS
        +TOOL_SPECS: tuple~ToolSpec~
        +TOOL_NAMES
        +tool_groups() dict
        +enabled_specs(config) list~ToolSpec~
    }

    class rest_tools_py {
        +build_input_model(RestTool)
        +run_rest_tool(RestTool, ctx, args) dict
        +spec_for(RestTool) ToolSpec
    }

    class server_py {
        +create_server(config, qlik_client?, engine_client?) MCPServer
        +run_server(config)
        +transport_security_for(config)
        +simplify_schema(schema) dict
        +build_http_app(mcp, config)
        -_make_tool_function(spec, ctx)
        -_guarded(tool_name, call) dict
    }

    class BearerAuthMiddleware {
        +__call__(scope, receive, send)
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
    registry_py --* ToolSpec
    rest_tools_py ..> RestTool : executes
    rest_tools_py ..> ToolSpec : builds
    registry_py --> rest_tools_py
    server_py --> registry_py
    server_py ..> BearerAuthMiddleware : wraps HTTP app
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
Each tool module exposes a `handle_*` coroutine that accepts a plain dict and returns a plain dict, plus a `ToolSpec` naming the tool, its description, its Pydantic input model, and whether it writes. `tools/registry.py` orders the specs. For each enabled spec, `server.py` builds a wrapper function whose `__signature__` is generated from the model's fields (types, descriptions, constraints, defaults) and registers it with `MCPServer.add_tool()`. The SDK derives the JSON Schema from that signature, so the Pydantic model is the single source of truth and adding a tool means adding one spec.

### Declarative REST tools
`RestTool` records (method, path template, `P` parameters with a location of path, query, body, or local, and an optional result shaper or custom coroutine) describe most platform tools. `build_input_model` turns the parameters into a Pydantic model with `create_model`, so the same signature and schema machinery serves engine and REST tools alike; `run_rest_tool` fills the path (rejecting unexpected characters), camel-cases query and body names, skips absent and false parameters, and maps HTTP errors to hints.

### Session pool
`EngineClient` keeps a `_PooledSession` per app with its own lock. `open_app` acquires the lock, reconnects if the session expired or broke, yields the `EngineSession`, then destroys temporary objects and alternate states before releasing. Idle and overflow sessions are closed as calls complete.

### Raw engine results are wrapped
The JSON-RPC engine returns `{"qLayout": ...}`, `{"qDataPages": [...]}`, `{"qResult": ...}`, `{"qList": [...]}`, and `{"qProp": ...}`. `EngineSession._unwrap` strips the wrapper while tolerating already-flat values, and the test fakes answer in the wrapped form so the tests exercise the real path.

### Pydantic Input Validation
Tool inputs are validated twice: by the SDK against the generated schema, and again by the Pydantic model inside the handler (which also runs custom validators such as the allowed resource types). Validation failures become `{"error": "Invalid input: ..."}` payloads.

### Error Sanitization
`_guarded()` in `server.py` maps failures to agent-readable payloads. Engine and REST errors keep their message (bounded to 500 characters). Authentication and unexpected errors are logged in full but reported generically so credentials and stack details never reach the model.

### MCP SDK Integration
The `mcp` SDK handles protocol serialization, transport negotiation, structured output, and schema exposure. The server only implements the tool functions and picks the transport in `run_server()`.
