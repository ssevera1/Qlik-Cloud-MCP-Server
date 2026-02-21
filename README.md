# Qlik Cloud MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes Qlik Cloud capabilities as standardized tools for AI agents. It enables LLMs (Claude, Gemini, GPT, etc.) to search the Qlik catalog, retrieve governed data, inspect dashboards, and create dynamic analysis views — all through a single, standardized interface.

## Architecture

```
┌───────────────────────┐     ┌────────────────────────┐     ┌─────────────────┐
│                       │     │                        │     │                 │
│     AI Agent          │────▶│  Qlik Cloud MCP Server │────▶│   Qlik Cloud    │
│  (Claude, Gemini,     │ MCP │                        │ API │                 │
│   LangChain, etc.)    │◀────│  stdio / SSE transport │◀────│  Engine + REST  │
│                       │     │                        │     │                 │
└───────────────────────┘     └────────────────────────┘     └─────────────────┘
```

The MCP server translates standardized tool calls into Qlik Cloud REST API and Engine API (WebSocket) operations. All data access is governed by Qlik's Section Access security rules.

## Tools

| Tool | Description | Qlik API |
|------|-------------|----------|
| `qlik_search` | Search the catalog for apps, datasets, and data products | REST API |
| `qlik_get_sheet_details` | Inspect existing dashboard layouts and visualizations | Engine API |
| `qlik_get_hypercube_data` | Retrieve aggregated data with dimensions and measures | Engine API |
| `qlik_create_sheet` | Dynamically build temporary analysis sheets | Engine API |

### qlik_search

Find apps and data products across the Qlik Cloud catalog. Use this for **metric discovery** — locating which apps contain the data you need.

```json
{
  "query": "revenue dashboard",
  "resource_type": "app",
  "limit": 10
}
```

### qlik_get_sheet_details

Inspect what dashboards already exist in an app. Use this **before creating new sheets** to avoid duplicating existing analysis.

```json
{
  "app_id": "abc-123-def",
  "sheet_id": "optional-specific-sheet"
}
```

### qlik_get_hypercube_data

Retrieve governed data as a table. Define dimensions (grouping columns) and measures (aggregations). All data respects Qlik Section Access.

```json
{
  "app_id": "abc-123-def",
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

Build a temporary analysis view with visualizations when no existing dashboard answers the question.

```json
{
  "app_id": "abc-123-def",
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
pip install -e .
```

### 2. Configure

```bash
# Create config from template
cp config.example.yaml config.yaml

# Set your Qlik Cloud API key
export QLIK_API_KEY="your-api-key-here"

# Edit config.yaml with your tenant URL
```

### 3. Validate

```bash
qlik-mcp-server --validate
```

### 4. Run

```bash
# stdio transport (for Claude Code, local agents)
qlik-mcp-server

# SSE transport (for remote agents)
qlik-mcp-server --transport sse --port 8080
```

## Integration with Claude Code

Add to your Claude Code MCP settings (`~/.claude/settings.json` or project `.claude/settings.json`):

```json
{
  "mcpServers": {
    "qlik-cloud": {
      "command": "python",
      "args": ["-m", "qlik_mcp_server"],
      "env": {
        "QLIK_API_KEY": "your-api-key",
        "QLIK_TENANT_URL": "https://your-tenant.us.qlikcloud.com"
      }
    }
  }
}
```

Or with a config file:

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

## Integration with LangChain

Using `langchain-mcp-adapters`:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_google_vertexai import ChatVertexAI

# Connect to the MCP server
async with MultiServerMCPClient({
    "qlik": {
        "url": "http://localhost:8080/sse",
        "transport": "sse",
    }
}) as client:
    tools = client.get_tools()

    # Use with Gemini on Vertex AI
    llm = ChatVertexAI(model="gemini-pro")
    agent = create_react_agent(llm, tools)

    result = await agent.ainvoke({
        "input": "What are the top 5 regions by revenue?"
    })
```

## Authentication

### API Key (Default)

1. Go to Qlik Cloud Hub → your profile → **API Keys**
2. Generate a new API key
3. Set via config or environment variable:

```yaml
qlik:
  api_key: ${QLIK_API_KEY}
```

### OAuth2 M2M (Production)

For production deployments with automatic token rotation:

```yaml
qlik:
  oauth:
    client_id: ${QLIK_OAUTH_CLIENT_ID}
    client_secret: ${QLIK_OAUTH_CLIENT_SECRET}
    token_url: https://your-tenant.us.qlikcloud.com/oauth/token
```

## Security

- All data access is **governed by Qlik Section Access**. The MCP server impersonates the service account's permissions — it can only see what that account is authorized to see.
- The MCP server **never bypasses** security rules. If the service account lacks access to a field or row, the tool returns empty results.
- API keys and OAuth credentials are stored in environment variables, not in code.
- Sheet creation is prefixed with `[Agent]` to distinguish agent-created content from human-created content.
- Sheet creation can be disabled in configuration (`tools.allow_sheet_creation: false`).

## Configuration Reference

See `config.example.yaml` for all options. Key settings:

```yaml
qlik:
  tenant_url: https://your-tenant.us.qlikcloud.com
  api_key: ${QLIK_API_KEY}
  timeout_seconds: 30

server:
  transport: stdio              # stdio | sse
  sse_port: 8080

tools:
  get_sheet_details: true       # Enable/disable individual tools
  get_hypercube_data: true
  create_sheet: true
  search: true
  max_hypercube_rows: 10000     # Row limit for data retrieval
  allow_sheet_creation: true    # Can be disabled for read-only mode
  created_sheet_prefix: "[Agent] "
```

## CLI Reference

```
qlik-mcp-server [OPTIONS]

Options:
  -c, --config FILE     Config file path (default: config.yaml)
  --transport MODE      Transport: stdio or sse (overrides config)
  --port PORT           SSE port (overrides config)
  -v, --verbose         Debug logging
  --validate            Check config and exit
  --version             Show version
```

## Project Structure

```
src/qlik_mcp_server/
  server.py               MCP server definition and tool dispatch
  cli.py                  CLI entry point
  config.py               YAML configuration with env var resolution
  auth.py                 API key + OAuth2 M2M authentication
  qlik_cloud_client.py    Qlik Cloud REST API client (search, catalog)
  engine_client.py        Qlik Engine API WebSocket client (hypercubes, sheets)
  tools/
    search.py             qlik_search — catalog discovery
    get_sheet_details.py  qlik_get_sheet_details — dashboard inspection
    get_hypercube_data.py qlik_get_hypercube_data — governed data retrieval
    create_sheet.py       qlik_create_sheet — dynamic sheet creation

design/
  c4-diagrams/            C4 architecture diagrams (Mermaid.js)
  adrs/                   Architecture Decision Records
```

## Design Documentation

- **C4 Diagrams**: `design/c4-diagrams/` — Context, Container, Component, Code levels
- **ADRs**: `design/adrs/`
  - ADR-001: Official MCP SDK over custom JSON-RPC
  - ADR-002: WebSocket Engine API for hypercube data
  - ADR-003: API Key default auth with OAuth2 M2M alternative
  - ADR-004: Stateless tool calls over persistent sessions
  - ADR-005: Four focused tools over one generic tool

## Prerequisites

- Python 3.11+
- Qlik Cloud tenant with API access
- API key or OAuth2 M2M credentials

## License

MIT
