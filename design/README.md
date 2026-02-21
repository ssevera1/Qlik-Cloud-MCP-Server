# Design Documentation

Architecture documentation for the Qlik Cloud MCP Server — the standardized integration layer between AI agents and Qlik Cloud.

## Contents

### C4 Model Diagrams (`c4-diagrams/`)

| Level | File | Description |
|-------|------|-------------|
| C1 | `c1-system-context.md` | MCP Server and its external actors |
| C2 | `c2-container.md` | Runtime components: MCP protocol, Qlik API clients |
| C3 | `c3-component.md` | Internal tools, auth, and engine session management |
| C4 | `c4-code.md` | Class/function relationships and data flow |

### Architecture Decision Records (`adrs/`)

| ADR | Title | Status |
|-----|-------|--------|
| ADR-001 | Official MCP SDK over Custom JSON-RPC Implementation | Accepted |
| ADR-002 | WebSocket Engine API over REST-Only for Hypercube Data | Accepted |
| ADR-003 | API Key as Default Auth with OAuth2 M2M as Alternative | Accepted |
| ADR-004 | Stateless Tool Calls over Persistent Engine Sessions | Accepted |
| ADR-005 | Tool Granularity — Four Focused Tools over One Generic Tool | Accepted |

## Rendering Diagrams

All diagrams use [Mermaid.js](https://mermaid.js.org/) syntax. Render natively on GitHub, in VS Code with the Mermaid extension, or via `mmdc` CLI.
