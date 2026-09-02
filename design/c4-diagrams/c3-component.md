# C3: Component Diagram

Internal components and interaction sequences.

```mermaid
C4Component
    title Component Diagram: MCP Server Internals

    Container_Boundary(server, "Qlik Cloud MCP Server") {

        Component(mcpServer, "create_server / run_server", "server.py", "Builds the SDK MCPServer,<br/>registers tools,<br/>selects transport")

        Component(searchTool, "Search", "tools/search.py", "Catalog search across<br/>apps, datasets,<br/>data products")

        Component(fieldsTool, "GetFields", "tools/get_fields.py", "Lists data model fields<br/>with cardinality and<br/>source tables")

        Component(sheetTool, "GetSheetDetails", "tools/get_sheet_details.py", "Fetches sheet layouts,<br/>object ids, types,<br/>titles, grid positions")

        Component(hypercubeTool, "GetHypercubeData", "tools/get_hypercube_data.py", "Applies selections and<br/>requests governed data<br/>with dimensions and measures")

        Component(createSheetTool, "CreateSheet", "tools/create_sheet.py", "Builds a sheet with<br/>charts, lays them out,<br/>saves the app")

        Component(restClient, "QlikCloudClient", "qlik_cloud_client.py", "Async HTTP client<br/>for REST API")

        Component(engineClient, "EngineClient / EngineSession", "engine_client.py", "WebSocket JSON-RPC<br/>for Qlik Engine API")

        Component(authMgr, "AuthManager", "auth.py", "API key / OAuth2<br/>token management")

        Component(config, "Config", "config.py", "YAML or env config<br/>with validation")
    }

    Rel(mcpServer, searchTool, "Dispatches")
    Rel(mcpServer, fieldsTool, "Dispatches")
    Rel(mcpServer, sheetTool, "Dispatches")
    Rel(mcpServer, hypercubeTool, "Dispatches")
    Rel(mcpServer, createSheetTool, "Dispatches")
    Rel(mcpServer, config, "Reads config")

    Rel(searchTool, restClient, "GET /api/v1/items")
    Rel(fieldsTool, engineClient, "get_fields()")
    Rel(sheetTool, engineClient, "get_sheets() / describe_sheet()")
    Rel(hypercubeTool, engineClient, "apply_selections() + create_hypercube()")
    Rel(createSheetTool, engineClient, "create_sheet()")

    Rel(restClient, authMgr, "Auth headers")
    Rel(engineClient, authMgr, "Auth headers")
```

## Common engine session lifecycle

Every engine-backed tool follows the same envelope. It is shown once here and elided in the sequences below.

```mermaid
sequenceDiagram
    participant Tool as Tool handler
    participant Engine as Qlik Engine API

    Tool->>Tool: validate app_id is a UUID
    Tool->>Engine: WebSocket connect wss://tenant/app/{app_id}<br/>Authorization: Bearer ...
    Tool->>Engine: OpenDoc(app_id) on handle -1
    Engine-->>Tool: doc handle
    Note over Tool,Engine: tool-specific calls
    Tool->>Engine: Close WebSocket
```

## Tool Interaction Sequences

### qlik_search

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant REST as Qlik Cloud REST API

    Agent->>MCP: tools/call("qlik_search",<br/>{query, resource_type?, space?, limit?})
    MCP->>REST: GET /api/v1/items?query={q}<br/>&resourceType={type}&spaceId={space}&limit={n}
    REST-->>MCP: {data: [...]}
    MCP-->>Agent: {results: [{id, resource_id, name,<br/>resource_type, space_id, url, ...}]}
```

### qlik_get_fields

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_get_fields", {app_id})
    MCP->>Engine: CreateSessionObject({qFieldListDef: {...}})
    Engine-->>MCP: object handle
    MCP->>Engine: GetLayout(handle)
    Engine-->>MCP: qFieldList.qItems
    MCP-->>Agent: {fields: [{name, cardinality, tags, source_tables}]}
```

### qlik_get_sheet_details

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_get_sheet_details",<br/>{app_id, sheet_id?})

    alt sheet_id provided
        MCP->>Engine: GetObject(sheet_id)
        Engine-->>MCP: object handle
        MCP->>Engine: GetLayout(handle)
        Engine-->>MCP: layout (qMeta, cells, qChildList)
    else list all sheets
        MCP->>Engine: GetObjects({qTypes: ["sheet"]})
        Engine-->>MCP: {qList: [...]}
        loop each sheet
            MCP->>Engine: GetObject(id) + GetLayout(handle)
            Engine-->>MCP: layout
        end
    end

    MCP-->>Agent: sheet titles and objects<br/>(id, type, title, grid bounds)
```

### qlik_get_hypercube_data

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_get_hypercube_data",<br/>{app_id, dimensions, measures, filters?, max_rows?})
    MCP->>MCP: clamp max_rows, check column limit

    loop each filter
        MCP->>Engine: GetField(name)
        Engine-->>MCP: field handle
        MCP->>Engine: SelectValues(handle, values)
        Engine-->>MCP: qReturn (false when nothing matched)
    end

    MCP->>Engine: CreateSessionObject({qHyperCubeDef:<br/>{qDimensions, qMeasures, qInitialDataFetch}})
    Engine-->>MCP: object handle
    MCP->>Engine: GetLayout(handle)
    Engine-->>MCP: qHyperCube (dimension/measure info,<br/>qSize, first data page)

    opt more rows needed
        MCP->>Engine: GetHyperCubeData(handle, "/qHyperCubeDef", [page])
        Engine-->>MCP: next page
    end

    MCP-->>Agent: {headers, data, row_count, total_rows,<br/>truncated, filters_applied,<br/>filters_not_matched, table}
```

### qlik_create_sheet

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_create_sheet",<br/>{app_id, title, description?, objects[]})
    MCP->>MCP: check allow_sheet_creation, validate types, prefix title

    MCP->>Engine: CreateObject({qInfo: {qType: "sheet"},<br/>qMetaDef: {title, description},<br/>columns: 24, rows: 12, cells: []})
    Engine-->>MCP: sheet handle + id

    loop each visualization object
        MCP->>Engine: CreateChild(sheet_handle,<br/>{qInfo.qType, visualization, title, qHyperCubeDef})
        Engine-->>MCP: child id (or error, recorded in failed_objects)
    end

    MCP->>Engine: GetProperties(sheet_handle)
    MCP->>Engine: SetProperties(sheet_handle, props + cells grid)
    MCP->>Engine: DoSave() on doc handle
    Engine-->>MCP: saved

    MCP-->>Agent: {sheet_id, url, object_count,<br/>objects, failed_objects, saved: true}
```
