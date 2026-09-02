# ADR-005: Tool Granularity: Focused Tools over One Generic Tool

**Status:** Accepted
**Date:** 2026-02-21

## Context

We need to decide how many MCP tools to expose and how specific each should be. Options range from one generic "qlik_query" tool to many fine-grained tools for each API operation.

## Decision

**Chosen: a small set of focused tools**, each mapping to a distinct capability. Four at the original decision, five since 2026-09-02:

| Tool | Capability |
|------|------------|
| `qlik_search` | Discover apps and data products |
| `qlik_get_fields` | Discover field names in an app's data model |
| `qlik_get_sheet_details` | Read existing dashboard layouts |
| `qlik_get_hypercube_data` | Retrieve governed data slices |
| `qlik_create_sheet` | Build and save analysis sheets |

## Rationale

1. **LLM tool selection works best with clear, distinct purposes**: When an LLM sees four tools with distinct descriptions, it can reliably select the right one. A single generic tool forces the LLM to construct complex nested parameters, increasing hallucination risk.

2. **Input validation is tool-specific**: `qlik_get_hypercube_data` requires `dimensions` and `measures` arrays. `qlik_search` requires a `query` string. Different schemas = different validation = clearer error messages.

3. **Security boundaries**: `qlik_create_sheet` (write operation) can be independently disabled via config, while keeping read tools active. A monolithic tool cannot offer this granularity.

4. **Not too many, not too few**: More than ~6 tools increases the cognitive load on the LLM (which tools overlap? which to use?). Fewer than 3 forces overloading. Four to five is the sweet spot for this domain.

### Trade-offs accepted:

- **No raw API access**: Advanced users cannot make arbitrary Engine API calls. They must use the predefined tools. This is intentional; it constrains the attack surface.
- **Composite operations require multiple calls**: If an agent wants to "search for an app, then get its data," it makes two tool calls. This is by design (stateless tools).

## Consequences

- Each tool has its own input schema (Pydantic model), handler function, and error handling
- Tools can be independently enabled/disabled in `config.yaml`
- Tool descriptions must be carefully written for LLM consumption (clear, non-overlapping)
- Future tools (e.g., `qlik_create_bookmark`, `qlik_get_variables`) can be added without modifying existing ones

## Update 2026-09-02

A fifth tool, `qlik_get_fields`, was added. Agents had no way to discover valid field names before calling `qlik_get_hypercube_data`, which made the primary data tool guesswork on unfamiliar apps. Field discovery is a distinct read-only capability (it maps to the engine's field list), so it fits the one-tool-per-capability rule and stays within the "fewer than about six tools" guidance. The tool names continue to mirror the naming used by Qlik's own hosted MCP server (`qlik_search`, `qlik_get_sheet_details`, `qlik_create_sheet`, `qlik_get_fields`).

## Update 2026-09-02 (later the same day): parity with Qlik's hosted MCP server

Qlik's hosted MCP server exposes roughly ninety tools across analytics apps, automations, data products, datasets, glossaries, lineage, and knowledge bases. Agents and prompts written against it expect a particular vocabulary. This server now implements the analytics-app subset with the same names and semantics, sixteen tools in five groups:

| Group | Tools |
|-------|-------|
| Discover | `qlik_search`, `qlik_describe_app` |
| Data model | `qlik_get_fields`, `qlik_get_field_values`, `qlik_search_field_values`, `qlik_list_dimensions`, `qlik_list_measures`, `qlik_list_bookmarks` |
| Dashboards | `qlik_list_sheets`, `qlik_get_sheet_details`, `qlik_get_chart_info`, `qlik_get_chart_data` |
| Compute | `qlik_get_hypercube_data` (with `filters` and `bookmark_id`) |
| Build | `qlik_create_sheet`, `qlik_add_chart`, `qlik_add_filter` |

Why sixteen is still consistent with this ADR: each tool maps to exactly one capability with a distinct schema, the tools are ordered as a workflow so selection is easy, and any tool can be hidden with `tools.disabled_tools`. Current models select reliably among tools of this count when descriptions do not overlap; the "about six" guidance above reflected 2025-era concerns and is superseded.

Deliberately out of scope, and why:

- **Selection tools** (`qlik_select_values`, `qlik_clear_selections`, `qlik_get_current_selections`): ADR-004 keeps calls stateless, so selections are parameters of `qlik_get_hypercube_data` (filters, bookmark) rather than session state.
- **Create, update, delete of master items, bookmarks, and apps**: the write surface stays additive (three tools that only add objects). Deletion and mutation of governed definitions by an agent needs a human approval loop this server does not provide.
- **Automations, data products, datasets, glossaries, lineage, knowledge bases**: different REST APIs and different personas; Qlik's hosted server covers them.

The registry (`tools/registry.py`) is the single place to add a tool: one `ToolSpec` with a Pydantic input model and a handler.
