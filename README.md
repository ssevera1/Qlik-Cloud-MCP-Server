# Qlik Cloud MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes Qlik Cloud capabilities as standardized tools for AI agents. It lets LLMs (Claude, Gemini, GPT, and others) search the Qlik catalog, discover fields, retrieve governed data, inspect dashboards, and create analysis sheets through one standard interface.

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

Qlik now ships its own hosted MCP server at `https://<tenant>/api/ai/mcp`, authenticated with an OAuth client and metered against your tenant's question capacity. This project is an independent, self-hosted alternative that talks directly to the Engine and REST APIs with a service account. Use Qlik's server when you want the full breadth of tools and per-user permissions; use this one when you want a small, auditable tool surface, service-account access, or no dependency on the hosted feature. The tool names here mirror Qlik's naming where the capabilities overlap.

## Tools

| Tool | Description | Qlik API |
|------|-------------|----------|
| `qlik_search` | Search the catalog for apps, datasets, and data products | REST API (`/api/v1/items`) |
| `qlik_get_fields` | List the fields in an app's data model | Engine API (field list) |
| `qlik_get_sheet_details` | Inspect existing dashboard layouts and visualizations | Engine API (`GetObjects`, `GetLayout`) |
| `qlik_get_hypercube_data` | Retrieve aggregated data with dimensions and measures | Engine API (`CreateSessionObject`) |
| `qlik_create_sheet` | Build an analysis sheet with charts and save the app | Engine API (`CreateObject`, `CreateChild`, `DoSave`) |

Every tool returns a JSON object. On failure the object contains an `error` string and usually a `hint`, so the agent can recover instead of crashing.

### qlik_search

Find apps and data products across the Qlik Cloud catalog. Use this for metric discovery: locating which apps contain the data you need. For apps, the `resource_id` in each result is the `app_id` to pass to the other tools.

```json
{
  "query": "revenue dashboard",
  "resource_type": "app",
  "limit": 10
}
```

### qlik_get_fields

List the user-visible fields of an app with cardinality, tags, and source tables. Call this before building a hypercube so dimension names and filter fields are real.

```json
{
  "app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d"
}
```

### qlik_get_sheet_details

Inspect what dashboards already exist in an app. Use this before creating new sheets to avoid duplicating existing analysis. With a `sheet_id`, returns each visualization's id, type, title, and grid position.

```json
{
  "app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d",
  "sheet_id": "optional-specific-sheet"
}
```

### qlik_get_hypercube_data

Retrieve governed data as a table. Define dimensions (grouping columns) and measures (aggregations). All data respects Qlik Section Access. Filters that match no values are reported back in `filters_not_matched` with a warning rather than silently ignored.

```json
{
  "app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d",
  "dimensions": ["Region", "Product Category"],
  "measures": ["Sum(Revenue)", "Count(OrderID)"],
  "filters": [
    {"field": "Year", "values": ["2025"]},
    {"field": "Region", "values": ["East", "West"]}
  ],
  "max_rows": 500
}
```

### qlik_create_sheet

Build an analysis sheet with visualizations when no existing dashboard answers the question. Objects are laid out automatically on the sheet grid, and the app is saved so the sheet persists.

```json
{
  "app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d",
  "title": "Q4 Revenue Analysis",
  "objects": [
    {
      "type": "barchart",
      "title": "Revenue by Region",
      "dimensions": ["Region"],
      "measures": ["Sum(Revenue)"]
    },
    {
      "type": "kpi",
      "title": "Total Revenue",
      "measures": ["Sum(Revenue)"]
    }
  ]
}
```

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

Or skip the file and use environment variables only: `QLIK_TENANT_URL` plus either `QLIK_API_KEY` or `QLIK_OAUTH_CLIENT_ID` and `QLIK_OAUTH_CLIENT_SECRET`.

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
- App and sheet identifiers are validated as UUIDs before any URL is built from them.
- API keys and OAuth credentials come from environment variables or the config file, never from code. The OAuth token URL must live on the configured tenant.
- Error responses to the agent are sanitized. Full details go to the server log on stderr.
- Sheets created by agents are prefixed (default `[Agent] `) to distinguish them from human-created content, and sheet creation can be disabled with `tools.allow_sheet_creation: false`.
- Read-only tools carry the MCP `readOnlyHint` annotation so clients can apply their own policies.
- The tenant URL must be a bare https origin (no path, query, or embedded credentials), since every REST and WebSocket URL is derived from it.
- When the HTTP transport is bound to localhost, the SDK's DNS-rebinding protection is enabled so a web page in your browser cannot reach the server. On other bind addresses, host validation is left to your reverse proxy.
- Engine error text returned to the agent is capped at 500 characters, and rate-limit backoff honors `Retry-After` up to 60 seconds.

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
  search: true                  # Enable/disable individual tools
  get_fields: true
  get_sheet_details: true
  get_hypercube_data: true
  create_sheet: true
  max_hypercube_rows: 10000     # Row limit for data retrieval
  allow_sheet_creation: true    # Can be disabled for read-only mode
  created_sheet_prefix: "[Agent] "
```

The older `sse_host` and `sse_port` keys are still accepted and map onto `http_host` and `http_port`.

## CLI Reference

```
qlik-mcp-server [OPTIONS]

Options:
  -c, --config FILE     Config file path (default: config.yaml)
  --transport MODE      stdio, streamable-http, or sse (overrides config)
  --host HOST           HTTP bind host (overrides config)
  --port PORT           HTTP port (overrides config)
  -v, --verbose         Debug logging
  --validate            Check config and exit
  --version             Show version
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests run against in-memory fakes of the Engine WebSocket and the REST API, so no tenant is needed. The suite covers the engine wire format, tool handlers, config validation, transport-level failures, and security guards (identifier validation, tenant and token URL checks, error sanitization).

Lint with:

```bash
pip install ruff
ruff check src tests
```

## Project Structure

```
src/qlik_mcp_server/
  server.py               MCP server definition and tool registration (MCPServer)
  cli.py                  CLI entry point
  config.py               YAML configuration with env var resolution
  auth.py                 API key + OAuth2 M2M authentication
  qlik_cloud_client.py    Qlik Cloud REST API client (search, catalog)
  engine_client.py        Qlik Engine API WebSocket client (hypercubes, fields, sheets)
  tools/
    search.py             qlik_search: catalog discovery
    get_fields.py         qlik_get_fields: data model field discovery
    get_sheet_details.py  qlik_get_sheet_details: dashboard inspection
    get_hypercube_data.py qlik_get_hypercube_data: governed data retrieval
    create_sheet.py       qlik_create_sheet: sheet creation

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
  - ADR-005: Focused tools over one generic tool

## Prerequisites

- Python 3.11+
- Qlik Cloud tenant with API access
- API key or OAuth2 M2M credentials

## License

MIT
