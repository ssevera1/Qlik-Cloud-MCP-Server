# Qlik Cloud MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes Qlik Cloud analytics capabilities as standardized tools for AI agents. It lets LLMs (Claude, Gemini, GPT, and others) search the Qlik catalog, understand an app's data model and governed definitions, read existing dashboards, compute governed data, and build new sheets through one standard interface.

Built on the official MCP Python SDK v2. Supports the stdio transport for local agents and Streamable HTTP for remote agents.

## Architecture

```
+-----------------------+     +------------------------+     +-----------------+
|                       |     |                        |     |                 |
|     AI Agent          |---->|  Qlik Cloud MCP Server |---->|   Qlik Cloud    |
|  (Claude, Gemini,     | MCP |                        | API |                 |
|   LangChain, etc.)    |<----|  stdio / Streamable    |<----|  Engine + REST  |
|                       |     |  HTTP transport        |     |                 |
+-----------------------+     +------------------------+     +-----------------+
```

The MCP server translates standardized tool calls into Qlik Cloud REST API and Engine API (WebSocket JSON-RPC) operations. All data access is governed by Qlik's Section Access rules.

## Relationship to Qlik's hosted MCP server

Qlik ships its own hosted MCP server at `https://<tenant>/api/ai/mcp`, authenticated per user with an OAuth client and metered against your tenant's question capacity. This project is an independent, self-hosted alternative that talks directly to the Engine and REST APIs with a service account.

The analytics-app tools here mirror the hosted server's names and semantics (`qlik_search`, `qlik_describe_app`, `qlik_get_fields`, `qlik_get_field_values`, `qlik_search_field_values`, `qlik_list_sheets`, `qlik_get_sheet_details`, `qlik_get_chart_info`, `qlik_get_chart_data`, `qlik_list_dimensions`, `qlik_list_measures`, `qlik_list_bookmarks`, `qlik_create_sheet`, `qlik_add_chart`, `qlik_add_filter`), so prompts transfer between the two. Qlik's server additionally covers automations, data products, datasets, glossaries, lineage, and knowledge bases, and it deletes and updates objects; those are deliberately out of scope here. Use Qlik's server for that breadth and per-user permissions; use this one for a small, auditable, read-mostly surface with service-account access and no dependency on the hosted feature.

## Tools

All tools take an `app_id` (except `qlik_search`) and return a JSON object. On failure the object contains an `error` string and usually a `hint`, so the agent can recover instead of crashing. Read-only tools carry the MCP `readOnlyHint` annotation.

### Discover

| Tool | What it does |
|------|--------------|
| `qlik_search` | Search the catalog for apps, datasets, data products, and other items. For apps, `resource_id` is the `app_id` for every other tool. |
| `qlik_describe_app` | Overview of an app: name, owner, space, last reload, Section Access flag, data model tables, and counts of sheets, fields, master items, and bookmarks. |

```json
{"query": "revenue dashboard", "resource_type": "app", "limit": 10}
```

### Understand the data model

| Tool | What it does |
|------|--------------|
| `qlik_get_fields` | Fields with cardinality, tags, and source tables. |
| `qlik_get_field_values` | Distinct values of one field with frequencies, optionally filtered by a substring. Use it to get exact spellings for filters. |
| `qlik_search_field_values` | Search all field values for terms and learn which fields contain them. |
| `qlik_list_dimensions` | Master (library) dimensions with their field definitions. |
| `qlik_list_measures` | Master (library) measures with their expressions, the governed way to compute a metric. |
| `qlik_list_bookmarks` | Saved selection sets, usable as `bookmark_id` in `qlik_get_hypercube_data`. |

```json
{"app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d", "field": "Region", "max_values": 50, "match": "ea"}
```

### Read existing dashboards

| Tool | What it does |
|------|--------------|
| `qlik_list_sheets` | Sheet ids, titles, descriptions, and published state (one engine call). |
| `qlik_get_sheet_details` | Charts on a sheet with id, type, title, and grid position; without `sheet_id`, all sheets. |
| `qlik_get_chart_info` | How a chart is defined: type, titles, dimensions, measure expressions, master item ids. |
| `qlik_get_chart_data` | The computed data behind a chart or list box, as a table. |

```json
{"app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d", "object_id": "JkYxDs"}
```

### Compute governed data

| Tool | What it does |
|------|--------------|
| `qlik_get_hypercube_data` | Aggregate by dimensions and measures, optionally under filters or a bookmark. Unmatched filters are reported in `filters_not_matched`. |

```json
{
  "app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d",
  "dimensions": ["Region", "Product Category"],
  "measures": ["Sum(Revenue)", "Count(OrderID)"],
  "filters": [{"field": "Year", "values": ["2025"]}],
  "bookmark_id": null,
  "max_rows": 500
}
```

### Build dashboards

| Tool | What it does |
|------|--------------|
| `qlik_create_sheet` | New sheet with charts laid out automatically; title prefixed with `[Agent]`; app saved. |
| `qlik_add_chart` | One chart appended below the existing content of a sheet; app saved. |
| `qlik_add_filter` | A filter pane with one list box per field on a sheet; app saved. |

```json
{
  "app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d",
  "title": "Q4 Revenue Analysis",
  "objects": [
    {"type": "barchart", "title": "Revenue by Region", "dimensions": ["Region"], "measures": ["Sum(Revenue)"]},
    {"type": "kpi", "title": "Total Revenue", "measures": ["Sum(Revenue)"]}
  ]
}
```

The three build tools are the only ones that change an app. They add objects and never delete or overwrite. Set `tools.allow_sheet_creation: false` to hide all three.

## Quick Start

### 1. Install

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -e .
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
export QLIK_API_KEY="your-api-key-here"
# Edit config.yaml and set qlik.tenant_url
```

Or skip the file and use environment variables only: `QLIK_TENANT_URL` plus either `QLIK_API_KEY` or `QLIK_OAUTH_CLIENT_ID` and `QLIK_OAUTH_CLIENT_SECRET`. `QLIK_MCP_DISABLED_TOOLS` takes a comma-separated list of tool names to hide.

### 3. Validate

```bash
qlik-mcp-server --validate
```

### 4. Run

```bash
# stdio transport (for Claude Code, Claude Desktop, local agents)
qlik-mcp-server

# Streamable HTTP transport (for remote agents), served at http://127.0.0.1:8080/mcp
qlik-mcp-server --transport streamable-http --port 8080
```

The HTTP endpoint carries no authentication of its own and uses your Qlik credentials for every caller. Keep it bound to localhost or put it behind an authenticating reverse proxy.

## Integration with Claude Code

Register the server from the project directory:

```bash
claude mcp add qlik-cloud -e QLIK_TENANT_URL=https://your-tenant.us.qlikcloud.com -e QLIK_API_KEY=your-api-key -- python -m qlik_mcp_server
```

Or add it to a project `.mcp.json` (Claude Code) or `claude_desktop_config.json` (Claude Desktop):

```json
{
  "mcpServers": {
    "qlik-cloud": {
      "command": "python",
      "args": ["-m", "qlik_mcp_server"],
      "env": {
        "QLIK_TENANT_URL": "https://your-tenant.us.qlikcloud.com",
        "QLIK_API_KEY": "your-api-key"
      }
    }
  }
}
```

With a config file instead of environment variables:

```json
{
  "mcpServers": {
    "qlik-cloud": {
      "command": "qlik-mcp-server",
      "args": ["-c", "/path/to/config.yaml"]
    }
  }
}
```

For a remote server, register the HTTP URL:

```bash
claude mcp add --transport http qlik-cloud http://127.0.0.1:8080/mcp
```

## Integration with LangChain

Using `langchain-mcp-adapters`:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

client = MultiServerMCPClient({
    "qlik": {
        "url": "http://localhost:8080/mcp",
        "transport": "streamable_http",
    }
})
tools = await client.get_tools()

llm = ChatVertexAI(model="gemini-2.5-pro")
agent = create_react_agent(llm, tools)

result = await agent.ainvoke({
    "messages": [("user", "What are the top 5 regions by revenue?")]
})
```

## Authentication

### API Key (Default)

1. In the Qlik Cloud hub, open your profile and choose **API keys**.
2. Generate a new API key.
3. Set it via config or environment variable:

```yaml
qlik:
  api_key: ${QLIK_API_KEY}
```

### OAuth2 M2M (Production)

For production deployments with automatic token rotation, create a machine-to-machine OAuth client in the Management Console and configure:

```yaml
qlik:
  oauth:
    client_id: ${QLIK_OAUTH_CLIENT_ID}
    client_secret: ${QLIK_OAUTH_CLIENT_SECRET}
```

The token endpoint defaults to `<tenant_url>/oauth/token`. Tokens are requested with the client credentials grant and cached until shortly before expiry.

## Security

- All data access is governed by Qlik Section Access. The MCP server acts as the service account and can only see what that account is authorized to see.
- The MCP server never bypasses security rules. If the service account lacks access to a field or row, the tool returns an error or empty results.
- The write surface is three additive tools (create sheet, add chart, add filter pane). Nothing deletes, publishes, or reloads.
- App identifiers are validated as full UUIDs before any URL is built from them. Object and sheet ids never reach a URL.
- The tenant URL must be a bare https origin (no path, query, or embedded credentials), since every REST and WebSocket URL is derived from it. The OAuth token URL must live on the configured tenant.
- API keys and OAuth credentials come from environment variables or the config file, never from code.
- Error responses to the agent are sanitized and engine error text is capped at 500 characters. Full details go to the server log on stderr.
- When the HTTP transport is bound to localhost, the SDK's DNS-rebinding protection is enabled so a web page in your browser cannot reach the server. On other bind addresses, host validation is left to your reverse proxy.
- Sheets created by agents are prefixed (default `[Agent] `) to distinguish them from human-created content.

## Configuration Reference

See `config.example.yaml` for all options. Key settings:

```yaml
qlik:
  tenant_url: https://your-tenant.us.qlikcloud.com
  api_key: ${QLIK_API_KEY}
  timeout_seconds: 30

server:
  transport: stdio              # stdio | streamable-http | sse
  http_host: 127.0.0.1
  http_port: 8080
  http_path: /mcp

tools:
  disabled_tools: []            # Names of tools to hide, e.g. [qlik_add_filter]
  allow_sheet_creation: true    # false hides create_sheet, add_chart, add_filter
  max_hypercube_rows: 10000     # Row limit for data retrieval
  max_hypercube_columns: 50
  created_sheet_prefix: "[Agent] "
```

The older per-tool booleans (`search`, `get_fields`, `get_sheet_details`, `get_hypercube_data`, `create_sheet`) and the `sse_host` / `sse_port` keys are still accepted.

## CLI Reference

```
qlik-mcp-server [OPTIONS]

Options:
  -c, --config FILE     Config file path (default: config.yaml)
  --transport MODE      stdio, streamable-http, or sse (overrides config)
  --host HOST           HTTP bind host (overrides config)
  --port PORT           HTTP port (overrides config)
  -v, --verbose         Debug logging
  --validate            Check config, list enabled tools, and exit
  --version             Show version
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

Tests run against in-memory fakes of the Engine WebSocket and the REST API, so no tenant is needed. The fakes answer in the raw engine wire format (results wrapped as `qLayout`, `qDataPages`, and so on). The suite covers the engine wire format, every tool end to end through the MCP server, config validation, transport-level failures, and security guards.

## Project Structure

```
src/qlik_mcp_server/
  server.py               MCP server: registry-driven tool registration (MCPServer)
  cli.py                  CLI entry point
  config.py               YAML / env configuration with validation
  auth.py                 API key + OAuth2 M2M authentication
  qlik_cloud_client.py    Qlik Cloud REST API client (catalog, app metadata)
  engine_client.py        Qlik Engine API WebSocket client (fields, values, charts, sheets, hypercubes)
  tools/
    registry.py           Ordered list of tool specs
    spec.py               ToolSpec / ToolContext
    search.py             qlik_search
    app_info.py           qlik_describe_app, qlik_list_sheets
    get_fields.py         qlik_get_fields
    field_values.py       qlik_get_field_values, qlik_search_field_values
    get_sheet_details.py  qlik_get_sheet_details
    chart.py              qlik_get_chart_info, qlik_get_chart_data
    master_items.py       qlik_list_dimensions, qlik_list_measures, qlik_list_bookmarks
    get_hypercube_data.py qlik_get_hypercube_data
    create_sheet.py       qlik_create_sheet
    sheet_edit.py         qlik_add_chart, qlik_add_filter

design/
  c4-diagrams/            C4 architecture diagrams (Mermaid.js)
  adrs/                   Architecture Decision Records
```

## Design Documentation

- C4 diagrams: `design/c4-diagrams/` (context, container, component, code)
- ADRs: `design/adrs/`
  - ADR-001: Official MCP SDK over custom JSON-RPC
  - ADR-002: WebSocket Engine API for hypercube data
  - ADR-003: API key default auth with OAuth2 M2M alternative
  - ADR-004: Stateless tool calls over persistent sessions
  - ADR-005: Focused tools, aligned with Qlik's hosted MCP server

## Prerequisites

- Python 3.11+
- Qlik Cloud tenant with API access
- API key or OAuth2 M2M credentials

## License

MIT
