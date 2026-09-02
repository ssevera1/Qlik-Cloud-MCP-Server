# C3: Component Diagram

Internal components and interaction sequences.

```mermaid
C4Component
    title Component Diagram: MCP Server Internals

    Container_Boundary(server, "Qlik Cloud MCP Server") {

        Component(mcpServer, "create_server / run_server", "server.py", "Builds the SDK MCPServer,<br/>registers tool specs,<br/>selects transport")

        Component(registry, "Tool registry", "tools/registry.py", "Ordered ToolSpecs;<br/>enable/disable and<br/>write gating")

        Component(discover, "Discover", "tools/search.py, tools/app_info.py", "qlik_search,<br/>qlik_describe_app,<br/>qlik_list_sheets")

        Component(model, "Data model", "tools/get_fields.py, tools/field_values.py, tools/master_items.py", "fields, field values,<br/>value search, master items,<br/>bookmarks")

        Component(dashboards, "Dashboards", "tools/get_sheet_details.py, tools/chart.py", "sheet details,<br/>chart info, chart data")

        Component(compute, "Compute", "tools/get_hypercube_data.py", "governed hypercube data<br/>with filters or bookmark")

        Component(build, "Build", "tools/create_sheet.py, tools/sheet_edit.py", "create sheet, add chart,<br/>add filter pane (writes)")

        Component(restClient, "QlikCloudClient", "qlik_cloud_client.py", "Async HTTP client<br/>for REST API")

        Component(engineClient, "EngineClient / EngineSession", "engine_client.py", "WebSocket JSON-RPC<br/>for Qlik Engine API")

        Component(authMgr, "AuthManager", "auth.py", "API key / OAuth2<br/>token management")

        Component(config, "Config", "config.py", "YAML or env config<br/>with validation")
    }

    Rel(mcpServer, registry, "enabled_specs(config)")
    Rel(mcpServer, config, "Reads config")
    Rel(registry, discover, "specs")
    Rel(registry, model, "specs")
    Rel(registry, dashboards, "specs")
    Rel(registry, compute, "specs")
    Rel(registry, build, "specs")

    Rel(discover, restClient, "GET /api/v1/items, /apps/{id}")
    Rel(discover, engineClient, "list_sheets(), master items")
    Rel(model, engineClient, "field list, list objects, SearchResults")
    Rel(dashboards, engineClient, "GetLayout, GetProperties, data pages")
    Rel(compute, engineClient, "ApplyBookmark, SelectValues, CreateSessionObject")
    Rel(build, engineClient, "CreateObject, CreateChild, SetProperties, DoSave")

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
    Engine-->>Tool: {qReturn: {qHandle: doc}}
    Note over Tool,Engine: tool-specific calls; results are unwrapped<br/>(qLayout, qDataPages, qResult, qList, qProp)
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

### qlik_describe_app

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant REST as Qlik Cloud REST API
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_describe_app", {app_id})
    MCP->>REST: GET /api/v1/apps/{app_id}
    REST-->>MCP: attributes (name, owner, reload time, section access)
    MCP->>REST: GET /api/v1/apps/{app_id}/data/metadata
    REST-->>MCP: tables, fields (optional; skipped on error)
    MCP->>Engine: GetObjects(sheets), master item lists, bookmark list
    Engine-->>MCP: counts
    MCP-->>Agent: overview with tables and counts
```

### qlik_get_fields, qlik_get_field_values, qlik_search_field_values

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_get_fields", {app_id})
    MCP->>Engine: CreateSessionObject({qFieldListDef})
    MCP->>Engine: GetLayout(handle)
    Engine-->>MCP: qFieldList.qItems
    MCP-->>Agent: {fields: [{name, cardinality, tags, source_tables}]}

    Agent->>MCP: tools/call("qlik_get_field_values", {app_id, field, max_values?, match?})
    MCP->>Engine: CreateSessionObject({qListObjectDef, qFrequencyMode: "V"})
    opt match given
        MCP->>Engine: SearchListObjectFor("/qListObjectDef", match)
    end
    MCP->>Engine: GetLayout(handle)
    Engine-->>MCP: qListObject.qDataPages (value, qState, qFrequency)
    MCP-->>Agent: {values: [{value, state, frequency}], total_values}

    Agent->>MCP: tools/call("qlik_search_field_values", {app_id, terms, fields?})
    MCP->>Engine: SearchResults({qSearchFields, qContext: "Cleared"}, terms, page)
    Engine-->>MCP: qResult.qSearchGroupArray
    MCP-->>Agent: {matches: [{field, values}]}
```

### qlik_list_dimensions, qlik_list_measures, qlik_list_bookmarks

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_list_measures", {app_id})
    MCP->>Engine: CreateSessionObject({qDimensionListDef, qMeasureListDef})
    MCP->>Engine: GetLayout(handle)
    Engine-->>MCP: qDimensionList / qMeasureList (qInfo, qMeta, qData)
    MCP-->>Agent: {measures: [{id, title, expression, tags}]}

    Agent->>MCP: tools/call("qlik_list_bookmarks", {app_id})
    MCP->>Engine: CreateSessionObject({qBookmarkListDef})
    MCP->>Engine: GetLayout(handle)
    Engine-->>MCP: qBookmarkList.qItems
    MCP-->>Agent: {bookmarks: [{id, title, sheet_id, selection_fields}]}
```

### qlik_list_sheets and qlik_get_sheet_details

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_list_sheets", {app_id})
    MCP->>Engine: GetObjects({qTypes: ["sheet"], qData: {rank: "/rank"}})
    Engine-->>MCP: {qList: [{qInfo, qMeta, qData}]}
    MCP-->>Agent: {sheets: [{id, title, description, published, rank}]}

    Agent->>MCP: tools/call("qlik_get_sheet_details", {app_id, sheet_id?})
    alt sheet_id provided
        MCP->>Engine: GetObject(sheet_id) + GetLayout(handle)
        Engine-->>MCP: layout (qMeta, cells, qChildList)
    else all sheets
        MCP->>Engine: GetObjects, then GetObject + GetLayout per sheet
    end
    MCP-->>Agent: objects (id, type, title, grid bounds)
```

### qlik_get_chart_info and qlik_get_chart_data

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_get_chart_info", {app_id, object_id})
    MCP->>Engine: GetObject(object_id)
    MCP->>Engine: GetProperties(handle) + GetLayout(handle)
    Engine-->>MCP: qHyperCubeDef (field defs, expressions, library ids),<br/>qHyperCube (titles)
    MCP-->>Agent: {type, title, dimensions, measures}

    Agent->>MCP: tools/call("qlik_get_chart_data", {app_id, object_id, max_rows?})
    MCP->>Engine: GetObject(object_id) + GetLayout(handle)
    alt hypercube (straight mode)
        loop until max_rows or all rows
            MCP->>Engine: GetHyperCubeData(handle, "/qHyperCubeDef", [page])
        end
    else list object
        MCP->>Engine: GetListObjectData(handle, "/qListObjectDef", [page])
    end
    MCP-->>Agent: {headers, data, row_count, total_rows, truncated, table}
```

### qlik_get_hypercube_data

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_get_hypercube_data",<br/>{app_id, dimensions, measures, filters?, bookmark_id?, max_rows?})
    MCP->>MCP: clamp max_rows, check column limit

    opt bookmark_id given
        MCP->>Engine: ApplyBookmark(bookmark_id)
        Engine-->>MCP: qSuccess (false is an error to the agent)
    end

    loop each filter
        MCP->>Engine: GetField(name) + SelectValues(handle, values)
        Engine-->>MCP: qReturn (false when nothing matched)
    end

    MCP->>Engine: CreateSessionObject({qHyperCubeDef:<br/>{qDimensions, qMeasures, qInitialDataFetch}})
    MCP->>Engine: GetLayout(handle)
    Engine-->>MCP: qHyperCube (info, qSize, first page)
    opt more rows needed
        MCP->>Engine: GetHyperCubeData(handle, "/qHyperCubeDef", [page])
    end

    MCP-->>Agent: {headers, data, total_rows, truncated,<br/>filters_applied, filters_not_matched, bookmark_applied, table}
```

### qlik_create_sheet, qlik_add_chart, qlik_add_filter

```mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant MCP as MCP Server
    participant Engine as Qlik Engine API

    Agent->>MCP: tools/call("qlik_create_sheet",<br/>{app_id, title, description?, objects[]})
    MCP->>Engine: CreateObject({qInfo.qType: "sheet", qMetaDef: {title},<br/>columns: 24, rows: 12, cells: []})
    loop each object
        MCP->>Engine: CreateChild(sheet_handle, {qInfo.qType, visualization,<br/>title, qHyperCubeDef})
    end
    MCP->>Engine: GetProperties + SetProperties(sheet, cells grid)
    MCP->>Engine: DoSave()
    MCP-->>Agent: {sheet_id, url, objects, failed_objects, saved: true}

    Agent->>MCP: tools/call("qlik_add_chart", {app_id, sheet_id, type, ...})
    MCP->>Engine: GetObject(sheet_id) + CreateChild(sheet, chart)
    MCP->>Engine: GetProperties + SetProperties(cells appended below existing)
    MCP->>Engine: DoSave()
    MCP-->>Agent: {object_id, url, saved: true}

    Agent->>MCP: tools/call("qlik_add_filter", {app_id, sheet_id, fields[]})
    MCP->>Engine: CreateChild(sheet, filterpane)
    loop each field
        MCP->>Engine: CreateChild(filterpane, listbox {qListObjectDef})
    end
    MCP->>Engine: SetProperties(cells) + DoSave()
    MCP-->>Agent: {filter_pane_id, listbox_ids, url, saved: true}
```
