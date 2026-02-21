# C1: System Context Diagram

The MCP Server as the bridge between AI agents and Qlik Cloud.

```mermaid
C4Context
    title System Context — Qlik Cloud MCP Server

    Person(developer, "Agent Developer", "Builds AI agents that need<br/>access to Qlik Cloud data")
    Person(endUser, "End User", "Asks questions via<br/>Teams/Slack/Chat")

    System(mcpServer, "Qlik Cloud MCP Server", "Exposes Qlik Cloud capabilities<br/>as standardized MCP tools:<br/>search, get data, create sheets")

    System_Ext(aiAgent, "AI Agent (Vertex AI /<br/>Claude / LangChain)", "LLM-powered reasoning engine<br/>that calls MCP tools to<br/>fulfill user requests")
    System_Ext(qlikCloud, "Qlik Cloud Tenant", "Managed BI platform:<br/>apps, dashboards, data engine,<br/>Section Access security")
    System_Ext(qlikEngine, "Qlik Associative Engine", "In-memory data engine that<br/>computes hypercubes,<br/>selections, aggregations")

    Rel(endUser, aiAgent, "Asks questions", "Natural language")
    Rel(developer, mcpServer, "Configures & deploys")
    Rel(aiAgent, mcpServer, "Calls tools", "MCP Protocol (JSON-RPC)")
    Rel(mcpServer, qlikCloud, "REST API calls", "HTTPS + API Key/OAuth")
    Rel(mcpServer, qlikEngine, "Engine API calls", "WebSocket JSON-RPC")
    Rel(qlikCloud, qlikEngine, "Hosts")
```

## Narrative

The **Qlik Cloud MCP Server** is a protocol translation layer. AI agents speak **MCP** (a standardized tool-calling protocol), and the server translates those calls into **Qlik Cloud REST API** and **Engine API** operations.

Key interactions:

| From | To | Protocol | Purpose |
|------|----|----------|---------|
| AI Agent | MCP Server | MCP (JSON-RPC over stdio/SSE) | Tool discovery and invocation |
| MCP Server | Qlik Cloud REST | HTTPS | App catalog, search, metadata |
| MCP Server | Qlik Engine | WebSocket JSON-RPC | Hypercube data, sheet operations |

### Security Model

The MCP server authenticates to Qlik Cloud using a **service account API key** (or OAuth2 M2M credentials). All data access is governed by Qlik's **Section Access** rules — if the service account cannot see certain data, the tool returns an empty result. The MCP server **never bypasses** Qlik security.
