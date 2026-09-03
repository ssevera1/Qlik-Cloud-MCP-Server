# Qlik Cloud MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that gives AI agents the whole of Qlik Cloud: analytics apps (fields, selections, governed data, sheets and charts, master items, bookmarks), the data catalog (datasets, data products, glossaries, lineage, trust and quality), automations, data alerts, AutoML, reloads, knowledge bases, and Qlik Answers. 107 tools in 19 groups, built for Claude Code over stdio and for Gemini Enterprise and other remote clients over authenticated Streamable HTTP.

Built on the official MCP Python SDK v2, with one engine session per app kept warm between calls, client-neutral tool schemas, and a test suite that drives every tool end to end against fakes that speak Qlik's real wire format.

## Architecture

```
+-----------------------+     +------------------------+     +-----------------+
|                       |     |                        |     |                 |
|     AI Agent          |---->|  Qlik Cloud MCP Server |---->|   Qlik Cloud    |
|  (Claude Code, Gemini | MCP |                        | API |                 |
|   Enterprise, ...)    |<----|  stdio / Streamable    |<----|  Engine + REST  |
|                       |     |  HTTP (bearer token)   |     |                 |
+-----------------------+     +------------------------+     +-----------------+
```

The server translates MCP tool calls into Qlik Cloud REST API and Engine API (WebSocket JSON-RPC) operations. All data access is governed by Qlik's Section Access rules.

## Relationship to Qlik's hosted MCP server

Qlik ships its own hosted MCP server at `https://<tenant>/api/ai/mcp`, authenticated per user with an OAuth client and metered against your tenant's question capacity. This project is an independent, self-hosted alternative that talks directly to the Engine and REST APIs with a service account. Tool names and semantics mirror the hosted server wherever the capability exists there, so prompts and agent memories transfer between the two.

What is different here: a persistent engine session per app (no reconnect per call), per-call filters that never leak into the session, compact JSON or markdown or CSV results, schemas that load in Gemini as well as Claude, profiles that size the catalog, and a bearer-token HTTP transport you control.

Five of Qlik's tools have no public API behind them and are not implemented: the automation "run display" (use `qlik_get_automation_run_log`, the run's exported log), starting an interactive automation run and answering its input prompt, connector webhook configuration, and browsing the tables of a data connection. `qlik_get_automation_inputs` is best effort, read from the automation's workspace definition.

## Tools

All tools return a JSON object. On failure it contains an `error` and usually a `hint`, so the agent can recover instead of crashing. Read-only tools carry the MCP `readOnlyHint` annotation, delete tools carry `destructiveHint`, and the selection tools are marked as session-state changes. `qlik-mcp-server --list-tools` prints the catalog for your configuration.

### Analytics apps (29 tools, engine-backed)

| Group | Tools |
|-------|-------|
| discover | `qlik_search`, `qlik_describe_app`, `qlik_get_app_script` |
| model | `qlik_get_fields`, `qlik_get_field_values`, `qlik_search_field_values` |
| dashboards | `qlik_list_sheets`, `qlik_get_sheet_details`, `qlik_get_chart_info`, `qlik_get_chart_data` |
| master_items | `qlik_list_dimensions`, `qlik_list_measures`, `qlik_create_dimension`, `qlik_update_dimension`, `qlik_delete_dimension`, `qlik_create_measure`, `qlik_update_measure`, `qlik_delete_measure` |
| bookmarks | `qlik_list_bookmarks`, `qlik_create_bookmark`, `qlik_select_bookmark`, `qlik_delete_bookmark` |
| selections | `qlik_select_values`, `qlik_clear_selections`, `qlik_get_current_selections` |
| compute | `qlik_create_data_object` |
| build | `qlik_create_sheet`, `qlik_add_chart`, `qlik_add_filter` |

`qlik_create_data_object` is the primary data tool: dimensions and measures in, aggregated rows out, with optional `filters` (isolated to the call), `bookmark_id`, `sort_by`, and `format` (`json` columns and rows, `markdown` table, or `csv`).

```json
{
  "app_id": "8b2f6c1e-3d4a-4f5b-9c6d-7e8f9a0b1c2d",
  "dimensions": ["Region", "Product Category"],
  "measures": ["Sum(Revenue)", "Count(OrderID)"],
  "filters": [{"field": "Year", "values": ["2025"]}],
  "sort_by": "Sum(Revenue)",
  "sort_descending": true,
  "max_rows": 200,
  "format": "markdown"
}
```

Selections made with `qlik_select_values` (exact values or a Qlik search pattern such as `Ea*` or `>1000`) persist on the app's session, like a user clicking in the app, until `qlik_clear_selections` or the session idles out. Every data tool reports the state it computed under, so nothing is invisible.

### Platform (78 tools, REST-backed)

| Group | Tools |
|-------|-------|
| automations | `qlik_list_automations`, `qlik_get_automation_by_id`, `qlik_get_automation_inputs`, `qlik_list_automation_runs`, `qlik_list_all_automation_runs`, `qlik_get_automation_run`, `qlik_fetch_automation_run` (waits), `qlik_get_automation_run_log`, `qlik_start_automation_run`, `qlik_stop_automation_run`, `qlik_retry_automation_run`, `qlik_create_automation`, `qlik_update_automation`, `qlik_set_automation_enabled`, `qlik_delete_automation`, `qlik_list_automation_connections`, `qlik_list_automation_connectors`, `qlik_get_automation_connector` |
| glossary | `qlik_list_glossaries`, `qlik_get_glossary`, `qlik_create_glossary`, `qlik_get_full_glossary_export`, `qlik_get_glossary_categories`, `qlik_create_glossary_category`, `qlik_search_glossary_terms`, `qlik_get_glossary_term`, `qlik_create_glossary_term`, `qlik_update_glossary_term`, `qlik_delete_glossary_term`, `qlik_update_term_status`, `qlik_get_glossary_term_links`, `qlik_create_glossary_term_links` |
| datasets | `qlik_get_dataset`, `qlik_get_dataset_schema`, `qlik_get_dataset_profile`, `qlik_get_dataset_sample`, `qlik_get_dataset_freshness`, `qlik_get_dataset_trust_score`, `qlik_get_dataset_memberships`, `qlik_update_dataset_metadata`, `qlik_update_dataset_quality`, `qlik_get_dataset_quality_computation_status`, `qlik_get_dataset_quality` |
| data_products | `qlik_list_data_products`, `qlik_get_data_product`, `qlik_get_data_product_documentation`, `qlik_create_data_product`, `qlik_update_data_product`, `qlik_update_data_product_space`, `qlik_update_activate_data_product`, `qlik_update_deactivate_data_product`, `qlik_delete_data_product` |
| lineage | `qlik_get_lineage` (upstream or downstream from a QRI, app, or dataset), `qlik_get_app_data_lineage` |
| knowledge | `qlik_list_knowledgebases`, `qlik_search_knowledgebase_chunks` |
| pipelines | `qlik_list_pipeline_projects`, `qlik_get_pipeline_project_details`, `qlik_get_pipeline_task_state`, `qlik_list_data_connections` |
| alerts | `qlik_list_data_alerts`, `qlik_get_data_alert`, `qlik_list_data_alert_executions`, `qlik_trigger_data_alert` |
| ml | `qlik_list_ml_experiments`, `qlik_get_ml_experiment`, `qlik_list_ml_experiment_models`, `qlik_list_ml_deployments`, `qlik_get_ml_deployment`, `qlik_run_ml_prediction` |
| reloads | `qlik_list_reloads`, `qlik_get_reload`, `qlik_start_reload`, `qlik_cancel_reload`, `qlik_get_app_reload_log` |
| spaces | `qlik_list_spaces`, `qlik_get_space` |
| answers | `qlik_ask_question` (Insight Advisor natural-language analysis) |

Endpoints and request shapes come from Qlik's own generated API client, not from prose docs, and the contract tests exercise every one of them.

### Sizing the catalog

The full catalog costs about 23k tokens of tool definitions per session. Pick what you need:

| Setting | Effect |
|---------|--------|
| `tools.profile: full` | Every tool (default) |
| `tools.profile: analytics` | The analytics-app groups plus lineage and answers, about 30 tools |
| `tools.profile: readonly` | Everything that reads; no creates, updates, deletes, runs, or reloads |
| `tools.disabled_groups: [automations, ml]` | Drop whole groups |
| `tools.disabled_tools: [qlik_delete_automation]` | Drop single tools |
| `tools.allow_writes: false` | Hide every tool that changes Qlik Cloud content |
| `tools.allow_sheet_creation: false` | Hide the three sheet-building tools |

The same switches exist as environment variables (`QLIK_MCP_PROFILE`, `QLIK_MCP_DISABLED_GROUPS`, `QLIK_MCP_DISABLED_TOOLS`, `QLIK_MCP_ALLOW_WRITES`) and `--profile` on the command line.

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
qlik-mcp-server --validate          # config, transport, profile, tool count
qlik-mcp-server --list-tools        # the catalog your configuration exposes
```

### 4. Run

```bash
# stdio transport (Claude Code, Claude Desktop, local agents)
qlik-mcp-server

# Streamable HTTP (Gemini Enterprise, remote agents), served at http://127.0.0.1:8080/mcp
QLIK_MCP_HTTP_BEARER_TOKEN="a-long-random-secret" qlik-mcp-server --transport streamable-http --port 8080
```

Set a bearer token whenever the HTTP endpoint is reachable beyond localhost; without one, anyone who can reach it can use your Qlik credentials. The server warns at startup when it binds beyond localhost without a token. `GET /healthz` is always open for load balancers.

## Claude Code

Register the server from the project directory:

```bash
claude mcp add qlik-cloud -e QLIK_TENANT_URL=https://your-tenant.us.qlikcloud.com -e QLIK_API_KEY=your-api-key -e QLIK_MCP_PROFILE=analytics -- python -m qlik_mcp_server
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
        "QLIK_API_KEY": "your-api-key",
        "QLIK_MCP_PROFILE": "full"
      }
    }
  }
}
```

For a remote server: `claude mcp add --transport http qlik-cloud https://mcp.example.com/mcp --header "Authorization: Bearer a-long-random-secret"`.

## Gemini Enterprise

Run the server with the Streamable HTTP transport behind HTTPS (a reverse proxy or your platform's ingress), set `server.http_bearer_token`, and prefer stateless mode:

```yaml
server:
  transport: streamable-http
  http_host: 0.0.0.0
  http_port: 8080
  http_bearer_token: ${QLIK_MCP_HTTP_BEARER_TOKEN}
  http_stateless: true
```

Register `https://your-host/mcp` as an MCP tool source in Gemini Enterprise with an `Authorization: Bearer <token>` header. Tool schemas are already in the subset Gemini accepts (no `$ref`, `$defs`, `anyOf`, or `title`), and a test enforces that for every tool.

## LangChain

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_google_vertexai import ChatVertexAI
from langgraph.prebuilt import create_react_agent

client = MultiServerMCPClient({
    "qlik": {
        "url": "http://localhost:8080/mcp",
        "transport": "streamable_http",
        "headers": {"Authorization": "Bearer a-long-random-secret"},
    }
})
tools = await client.get_tools()
agent = create_react_agent(ChatVertexAI(model="gemini-2.5-pro"), tools)
result = await agent.ainvoke({"messages": [("user", "What are the top 5 regions by revenue?")]})
```

## Performance

- **Warm engine sessions.** One WebSocket per app stays open between calls (`qlik.reuse_sessions`, idle timeout `qlik.session_idle_seconds`, pool cap `qlik.max_sessions`), which removes the connect and `OpenDoc` round trips, roughly half a second, from every call after the first. Calls to the same app are serialized on that socket; different apps run in parallel. A socket that fails mid-call is evicted, not reused.
- **Temporary objects are cleaned up** after each call so long sessions do not accumulate engine objects, and per-call filters run in a temporary alternate state.
- **REST metadata is cached** for `qlik.cache_ttl_seconds` (default 60) on the list and lookup tools that opt in, and the HTTP client pool is shared.
- **Compact results.** Tabular tools return `columns` and `rows` by default (or a markdown table or CSV on request) instead of duplicating data as text; responses above `tools.max_response_chars` are trimmed with a note.
- **Waiting is server-side.** `qlik_fetch_automation_run` polls a run to completion so the agent does not burn turns polling.

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

Create a machine-to-machine OAuth client in the Management Console and configure:

```yaml
qlik:
  oauth:
    client_id: ${QLIK_OAUTH_CLIENT_ID}
    client_secret: ${QLIK_OAUTH_CLIENT_SECRET}
```

The token endpoint defaults to `<tenant_url>/oauth/token`. Tokens are requested with the client credentials grant and cached until shortly before expiry. Note that the service account's roles and scopes decide what the tools can reach; automations, glossaries, data products, and AutoML each need their own entitlements.

## Security

- All data access is governed by Qlik Section Access. The server acts as the service account and can only see what that account is authorized to see; it never bypasses security rules.
- Writes are opt-out at three levels: `tools.allow_writes`, `tools.profile: readonly`, and per group or tool. Delete tools are annotated as destructive so clients can require confirmation.
- App identifiers are validated as full UUIDs before any URL is built from them; every other id is restricted to safe characters before it enters a path.
- The tenant URL must be a bare https origin, and the OAuth token URL must live on the configured tenant.
- The HTTP transport takes a bearer token compared in constant time, and on loopback binds the SDK's DNS-rebinding protection is on.
- Error responses to the agent are sanitized and engine error text is capped at 500 characters. Full details go to the server log on stderr.
- Sheets created by agents are prefixed (default `[Agent] `).

## Configuration Reference

See `config.example.yaml` for every option with comments. Environment variables: `QLIK_TENANT_URL`, `QLIK_API_KEY`, `QLIK_OAUTH_CLIENT_ID`, `QLIK_OAUTH_CLIENT_SECRET`, `QLIK_OAUTH_TOKEN_URL`, `QLIK_REUSE_SESSIONS`, `QLIK_MCP_TRANSPORT`, `QLIK_MCP_HTTP_HOST`, `QLIK_MCP_HTTP_PORT`, `QLIK_MCP_HTTP_BEARER_TOKEN`, `QLIK_MCP_HTTP_STATELESS`, `QLIK_MCP_PROFILE`, `QLIK_MCP_DISABLED_GROUPS`, `QLIK_MCP_DISABLED_TOOLS`, `QLIK_MCP_ALLOW_WRITES`.

The older `sse_host` and `sse_port` keys and the per-tool booleans (`search`, `get_fields`, `get_sheet_details`, `get_hypercube_data`, `create_sheet`) are still accepted. The tool formerly named `qlik_get_hypercube_data` is now `qlik_create_data_object`, matching Qlik's name.

## CLI Reference

```
qlik-mcp-server [OPTIONS]

Options:
  -c, --config FILE     Config file path (default: config.yaml)
  --transport MODE      stdio, streamable-http, or sse (overrides config)
  --host HOST           HTTP bind host (overrides config)
  --port PORT           HTTP port (overrides config)
  --profile PROFILE     full, analytics, or readonly (overrides config)
  -v, --verbose         Debug logging
  --validate            Check config and exit
  --list-tools          Print the enabled tools by group and exit
  --version             Show version
```

## Development

```bash
pip install -e ".[dev]"
pytest                                  # about 300 tests, no tenant needed
ruff check src tests
mypy --ignore-missing-imports src
```

Tests run against in-memory fakes of the Engine WebSocket and the REST API that answer in Qlik's raw wire format. They cover the engine protocol, every tool end to end through the MCP server, the REST request each platform tool builds, session pooling, schema simplification, HTTP bearer auth, config validation, and security guards.

## Project Structure

```
src/qlik_mcp_server/
  server.py               MCPServer build, schema simplification, HTTP app with bearer auth
  cli.py                  CLI entry point
  config.py               YAML / env configuration, profiles, validation
  auth.py                 API key + OAuth2 M2M authentication
  qlik_cloud_client.py    REST client: generic call(), cache, text downloads
  engine_client.py        Engine client: per-app session pool, all engine operations
  tools/
    registry.py           Ordered catalog and enable/disable logic
    spec.py               ToolSpec / ToolContext
    rest_tools.py         Declarative REST tool layer
    rest_catalog.py       The 78 REST-backed tools
    search.py, app_info.py, get_fields.py, field_values.py, get_sheet_details.py,
    chart.py, master_items.py, selections.py, get_hypercube_data.py,
    create_sheet.py, sheet_edit.py     The 29 engine-backed tools

design/
  c4-diagrams/            C4 architecture diagrams (Mermaid.js)
  adrs/                   Architecture Decision Records (001 to 006)
```

## Prerequisites

- Python 3.11+
- Qlik Cloud tenant with API access
- API key or OAuth2 M2M credentials

## License

MIT
