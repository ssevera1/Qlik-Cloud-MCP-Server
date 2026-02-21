# C3: Component Diagram

Internal components and interaction sequences.

```mermaid
C4Component
    title Component Diagram — MCP Server Internals

    Container_Boundary(server, "Qlik Cloud MCP Server") {

        Component(mcpServer, "MCPServer", "server.py", "MCP protocol handler,<br/>tool registration,<br/>transport management")

        Component(sheetTool, "GetSheetDetails", "tools/get_sheet_details.py", "Fetches sheet layouts,<br/>object definitions,<br/>visualization types")

        Component(hypercubeTool, "GetHypercubeData", "tools/get_hypercube_data.py", "Requests governed data<br/>slices with dimensions<br/>and measures")

        Component(createSheetTool, "CreateSheet", "tools/create_sheet.py", "Dynamically builds<br/>temporary analysis<br/>sheets with objects")

        Component(searchTool, "Search", "tools/search.py", "Traverses app catalog,<br/>finds data products<br/>and metric definitions")

        Component(restClient, "QlikCloudClient", "qlik_cloud_client.py", "Async HTTP client<br/>for REST API")

        Component(engineClient, "EngineClient", "engine_client.py", "WebSocket JSON-RPC<br/>for Qlik Engine API")

        Component(authMgr, "AuthManager", "auth.py", "API key / OAuth2<br/>token management")

        Component(config, "Config", "config.py", "YAML config loader<br/>with env var resolution")
    }

    Rel(mcpServer, sheetTool, "Dispatches")
    Rel(mcpServer, hypercubeTool, "Dispatches")
    Rel(mcpServer, createSheetTool, "Dispatches")
    Rel(mcpServer, searchTool, "Dispatches")
    Rel(mcpServer, config, "Reads config")

    Rel(sheetTool, engineClient, "getLayout()")
    Rel(hypercubeTool, engineClient, "createSessionObject()")
    Rel(createSheetTool, engineClient, "createSheet()")
    Rel(searchTool, restClient, "GET /api/v1/items")

    Rel(restClient, authMgr, "Auth headers")
    Rel(engineClient, authMgr, "Auth headers")
```

## Tool Interaction Sequences

### qlik_get_sheet_details

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_get_sheet_details",<br/>{app_id, sheet_id?})
    MCP->>Engine: WebSocket connect wss://tenant/app/{app_id}
    Engine-->>MCP: Connected (doc handle = -1)

    alt sheet_id provided
        MCP->>Engine: GetObject(sheet_id)
        Engine-->>MCP: Object handle
        MCP->>Engine: GetLayout(handle)
        Engine-->>MCP: Sheet layout JSON
    else list all sheets
        MCP->>Engine: GetObjects({qType: "sheet"})
        Engine-->>MCP: Sheet list
        loop each sheet
            MCP->>Engine: GetLayout(handle)
            Engine-->>MCP: Layout with child objects
        end
    end

    MCP->>Engine: Close WebSocket
    MCP-->>Agent: Sheet details (titles, objects, vis types)
```

### qlik_get_hypercube_data

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_get_hypercube_data",<br/>{app_id, dimensions, measures, filters?})
    MCP->>Engine: WebSocket connect
    Engine-->>MCP: Connected

    opt filters provided
        MCP->>Engine: ApplyBookmark or SelectValues
        Engine-->>MCP: Selection applied
    end

    MCP->>Engine: CreateSessionObject({<br/>  qHyperCubeDef: {<br/>    qDimensions, qMeasures,<br/>    qInitialDataFetch: [{rows, cols}]<br/>  }})
    Engine-->>MCP: Object handle + initial data

    opt more rows needed
        MCP->>Engine: GetHyperCubeData(handle, page)
        Engine-->>MCP: Next page of data
    end

    MCP->>Engine: Close WebSocket
    MCP-->>Agent: Tabular data (headers + rows)
```

### qlik_create_sheet

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_create_sheet",<br/>{app_id, title, objects[]})
    MCP->>Engine: WebSocket connect
    Engine-->>MCP: Connected

    MCP->>Engine: CreateObject({qType: "sheet",<br/>  title: "[Agent] {title}"})
    Engine-->>MCP: Sheet handle

    loop each visualization object
        MCP->>Engine: CreateChild(sheet_handle, {<br/>  qType: objectType,<br/>  qHyperCubeDef: {...}})
        Engine-->>MCP: Child handle
    end

    MCP->>Engine: Close WebSocket
    MCP-->>Agent: {sheet_id, url, object_count}
```

### qlik_search

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant REST as Qlik Cloud REST API

    Agent->>MCP: tools/call("qlik_search",<br/>{query, resource_type?, space?})
    MCP->>REST: GET /api/v1/items?query={q}<br/>&resourceType={type}
    REST-->>MCP: {data: [...], links: {next}}

    opt more pages
        MCP->>REST: GET {next_link}
        REST-->>MCP: Next page
    end

    MCP-->>Agent: [{id, name, type, space,<br/>  owner, updated, description}]
```
