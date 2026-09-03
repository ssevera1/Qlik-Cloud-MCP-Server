"""REST-backed tools: automations, glossary, datasets, data products, lineage,
knowledge bases, pipelines, alerts, AutoML, reloads, spaces, and Qlik Answers.

Endpoints and request shapes follow the Qlik Cloud REST API as published in
Qlik's own generated client (``@qlik/api``); see design/adrs/adr-005 for the
coverage decisions.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from ..qlik_cloud_client import QlikCloudError
from .rest_tools import (
    P,
    RestTool,
    items_of,
    json_patch,
    p_limit,
    path_param,
    pick,
    spec_for,
)
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

_APP_ID = P("app_id", "The Qlik Cloud app ID", str, required=True, where="local", max_length=64)
_TERMINAL_RUN_STATES = {"failed", "finished", "finished with warnings", "stopped", "exceeded limit", "must stop"}
_SCIM_VALUE_RE = re.compile(r'["\\]')


def _scim(value: str) -> str:
    """Quote a value for a SCIM filter expression."""
    return '"' + _SCIM_VALUE_RE.sub("", value) + '"'


def _error(tool: str, e: QlikCloudError, **context: Any) -> dict:
    logger.error("%s failed: %s", tool, e)
    return {"error": str(e), "tool": tool, **context}


# ══════════════════════════════════════════════════════════════════
# Automations
# ══════════════════════════════════════════════════════════════════

_AUTOMATION_KEYS = {
    "id": "id", "name": "name", "description": "description", "run_mode": "runMode",
    "state": "state", "last_run_status": "lastRunStatus", "last_run_at": "lastRunAt",
    "space_id": "spaceId", "owner_id": "ownerId", "created_at": "createdAt", "updated_at": "updatedAt",
    "max_concurrent_runs": "maxConcurrentRuns",
}
_RUN_KEYS = {
    "id": "id", "status": "status", "context": "context", "title": "title",
    "start_time": "startTime", "stop_time": "stopTime", "duration": "duration",
    "scheduled_start_time": "scheduledStartTime", "executed_by_id": "executedById", "error": "error",
}


def _automation_summary(item: dict) -> dict:
    out = pick(item, _AUTOMATION_KEYS)
    out["schedule_count"] = len(item.get("schedules") or [])
    return out


def _automation_detail(item: dict, include_workspace: bool) -> dict:
    out = _automation_summary(item)
    out["schedules"] = item.get("schedules") or []
    out["connector_ids"] = item.get("connectorIds") or []
    if item.get("lastRun"):
        out["last_run"] = pick(item["lastRun"], _RUN_KEYS)
    workspace = item.get("workspace")
    if include_workspace:
        out["workspace"] = workspace
    elif isinstance(workspace, dict):
        out["workspace_block_count"] = len(workspace.get("blocks") or [])
    return out


def _shape_automations(raw: Any, args: dict) -> dict:
    items = [_automation_summary(i) for i in items_of(raw)]
    return {"count": len(items), "automations": items}


def _shape_automation(raw: Any, args: dict) -> dict:
    return _automation_detail(raw or {}, bool(args.get("include_workspace")))


def _shape_runs(raw: Any, args: dict) -> dict:
    runs = [pick(r, _RUN_KEYS) for r in items_of(raw)]
    return {"automation_id": args.get("automation_id"), "count": len(runs), "runs": runs}


def _shape_run(raw: Any, args: dict) -> dict:
    return {"automation_id": args.get("automation_id"), **pick(raw or {}, _RUN_KEYS)}


def _walk(value: Any, seen: list, depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(value, dict):
        if any(k in value for k in ("inputs", "inputFields", "userInputs")):
            for key in ("inputs", "inputFields", "userInputs"):
                if isinstance(value.get(key), list):
                    seen.extend(v for v in value[key] if isinstance(v, dict))
        for child in value.values():
            _walk(child, seen, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _walk(child, seen, depth + 1)


async def _automation_inputs(ctx: ToolContext, args: dict) -> dict:
    """Best-effort extraction of declared inputs from an automation's workspace."""
    automation_id = args["automation_id"]
    try:
        raw = await ctx.qlik_client.call("GET", f"/api/v1/automations/{automation_id}")
    except QlikCloudError as e:
        return _error("qlik_get_automation_inputs", e, automation_id=automation_id)
    workspace = (raw or {}).get("workspace")
    blocks = workspace.get("blocks") if isinstance(workspace, dict) else None
    found: list = []
    _walk(workspace, found)
    inputs = []
    for entry in found:
        inputs.append({
            "name": entry.get("name") or entry.get("id") or entry.get("label"),
            "label": entry.get("label") or entry.get("displayName") or "",
            "type": entry.get("type") or entry.get("fieldType") or "",
            "required": bool(entry.get("required") or entry.get("isRequired")),
            "default": entry.get("default") or entry.get("defaultValue"),
        })
    block_types = sorted({str(b.get("type") or b.get("blockType") or "") for b in blocks or [] if isinstance(b, dict)})
    return {
        "automation_id": automation_id,
        "name": (raw or {}).get("name"),
        "run_mode": (raw or {}).get("runMode"),
        "inputs": inputs,
        "block_types": [t for t in block_types if t],
        "note": (
            "Inputs are read from the automation's workspace definition. Qlik's public API does not "
            "expose interactive input blocks, so a run started via the API cannot receive answers."
        ),
    }


async def _fetch_run(ctx: ToolContext, args: dict) -> dict:
    """Poll a run until it reaches a terminal state or the wait budget is spent."""
    automation_id, run_id = args["automation_id"], args["run_id"]
    budget = float(args.get("timeout_seconds") or 30)
    interval = 3.0
    deadline = asyncio.get_event_loop().time() + budget
    last: dict = {}
    while True:
        try:
            raw = await ctx.qlik_client.call("GET", f"/api/v1/automations/{automation_id}/runs/{run_id}")
        except QlikCloudError as e:
            return _error("qlik_fetch_automation_run", e, automation_id=automation_id, run_id=run_id)
        last = pick(raw or {}, _RUN_KEYS)
        status = str(last.get("status") or "").lower()
        if status in _TERMINAL_RUN_STATES:
            return {"automation_id": automation_id, "finished": True, **last}
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return {"automation_id": automation_id, "finished": False, **last,
                    "note": f"Still {status or 'pending'} after {int(budget)}s; call again to keep waiting."}
        await asyncio.sleep(min(interval, max(0.1, remaining)))


async def _run_log(ctx: ToolContext, args: dict) -> dict:
    automation_id, run_id = args["automation_id"], args["run_id"]
    try:
        raw = await ctx.qlik_client.call(
            "POST", f"/api/v1/automations/{automation_id}/runs/{run_id}/actions/export", json={},
        )
        url = (raw or {}).get("url") if isinstance(raw, dict) else None
        if not url:
            return {"automation_id": automation_id, "run_id": run_id, "log": "", "note": "No log export URL returned."}
        text = await ctx.qlik_client.fetch_text_url(url, max_chars=int(args.get("max_chars") or 20000))
    except QlikCloudError as e:
        return _error("qlik_get_automation_run_log", e, automation_id=automation_id, run_id=run_id)
    return {"automation_id": automation_id, "run_id": run_id, "length": len(text), "log": text}


async def _all_runs(ctx: ToolContext, args: dict) -> dict:
    """Recent runs across automations (no tenant-wide endpoint exists; this fans out)."""
    limit = int(args.get("limit") or 25)
    status = args.get("status")
    try:
        raw = await ctx.qlik_client.call("GET", "/api/v1/automations", params={"limit": 50, "sort": "-lastRunAt"})
    except QlikCloudError as e:
        return _error("qlik_list_all_automation_runs", e)
    automations = items_of(raw)[:50]
    semaphore = asyncio.Semaphore(6)

    async def runs_for(auto: dict) -> list[dict]:
        async with semaphore:
            try:
                params: dict = {"limit": 10, "sort": "-startTime"}
                if status:
                    params["filter"] = f"status eq {_scim(status)}"
                res = await ctx.qlik_client.call("GET", f"/api/v1/automations/{auto.get('id')}/runs", params=params)
            except QlikCloudError:
                return []
            return [{"automation_id": auto.get("id"), "automation_name": auto.get("name"), **pick(r, _RUN_KEYS)}
                    for r in items_of(res)]

    results = await asyncio.gather(*(runs_for(a) for a in automations))
    runs = [r for group in results for r in group]
    runs.sort(key=lambda r: str(r.get("start_time") or ""), reverse=True)
    return {"count": min(len(runs), limit), "automations_scanned": len(automations), "runs": runs[:limit]}


async def _get_connector(ctx: ToolContext, args: dict) -> dict:
    connector = args["connector"]
    try:
        raw = await ctx.qlik_client.call("GET", "/api/v1/automation-connectors", params={"limit": 200})
    except QlikCloudError as e:
        return _error("qlik_get_automation_connector", e)
    wanted = connector.lower()
    for item in items_of(raw):
        if str(item.get("id", "")).lower() == wanted or str(item.get("name", "")).lower() == wanted:
            return {**pick(item, {"id": "id", "name": "name", "description": "description",
                                  "has_webhooks": "hasWebhooks", "billable": "billable"}),
                    "note": "Webhook event configuration is not exposed by Qlik's public API." if item.get("hasWebhooks") else None}
    return {"error": f"Connector not found: {connector}", "hint": "List connectors with qlik_list_automation_connectors."}


async def _update_automation(ctx: ToolContext, args: dict) -> dict:
    automation_id = args["automation_id"]
    try:
        current = await ctx.qlik_client.call("GET", f"/api/v1/automations/{automation_id}") or {}
        body = {
            "name": args.get("name") or current.get("name"),
            "description": args.get("description") if args.get("description") is not None else current.get("description"),
            "maxConcurrentRuns": args.get("max_concurrent_runs") or current.get("maxConcurrentRuns"),
            "schedules": args.get("schedules") if args.get("schedules") is not None else [
                {k: v for k, v in s.items() if k in ("interval", "startAt", "stopAt", "timezone")}
                for s in current.get("schedules") or []
            ],
            "workspace": args.get("workspace") if args.get("workspace") is not None else current.get("workspace"),
        }
        raw = await ctx.qlik_client.call("PUT", f"/api/v1/automations/{automation_id}", json=body)
    except QlikCloudError as e:
        return _error("qlik_update_automation", e, automation_id=automation_id)
    return _automation_detail(raw or {}, False)


async def _set_automation_enabled(ctx: ToolContext, args: dict) -> dict:
    automation_id = args["automation_id"]
    action = "enable" if args.get("enabled", True) else "disable"
    try:
        raw = await ctx.qlik_client.call("POST", f"/api/v1/automations/{automation_id}/actions/{action}", json={})
    except QlikCloudError as e:
        return _error("qlik_set_automation_enabled", e, automation_id=automation_id)
    return {"automation_id": automation_id, "action": action, **pick(raw or {}, _AUTOMATION_KEYS)}


AUTOMATION_TOOLS = (
    RestTool(
        name="qlik_list_automations", title="List automations", group="automations",
        description="List Qlik automations the service account can see, with run mode, state, and last run status. Filter with SCIM syntax, e.g. name co \"Daily\" or lastRunStatus eq \"failed\".",
        method="GET", path="/api/v1/automations",
        params=(P("filter", "SCIM filter over name, runMode, lastRunStatus, ownerId, spaceId", max_length=512),
                p_limit(50, 200),
                P("sort", "Sort field with +/- prefix, e.g. -lastRunAt", max_length=40),
                P("list_all", "List every automation in the tenant (admins only)", bool)),
        result=_shape_automations, cache=True,
    ),
    RestTool(
        name="qlik_get_automation_by_id", title="Get automation", group="automations",
        description="Get an automation's definition: name, run mode, schedules, state, last run, and optionally the full workspace (block graph).",
        method="GET", path="/api/v1/automations/{automation_id}",
        params=(path_param("automation_id", "The automation id"),
                P("include_workspace", "Include the full workspace JSON (can be large)", bool, default=False)),
        result=_shape_automation,
    ),
    RestTool(
        name="qlik_get_automation_inputs", title="Get automation inputs", group="automations",
        description="Discover the input fields an automation declares (best effort, read from its workspace) before starting a run.",
        params=(path_param("automation_id", "The automation id"),), custom=_automation_inputs,
    ),
    RestTool(
        name="qlik_list_automation_runs", title="List automation runs", group="automations",
        description="Execution history for one automation: status, timing, context, and errors of recent runs.",
        method="GET", path="/api/v1/automations/{automation_id}/runs",
        params=(path_param("automation_id", "The automation id"), p_limit(20, 200),
                P("filter", "SCIM filter over status, context, startTime, title", max_length=512),
                P("sort", "Sort, default -startTime", default="-startTime", max_length=40)),
        result=_shape_runs,
    ),
    RestTool(
        name="qlik_list_all_automation_runs", title="List recent runs across automations", group="automations",
        description="Recent runs across all automations the account can see, newest first, optionally filtered by status (failed, finished, running, queued).",
        params=(p_limit(25, 200), P("status", "Only runs with this status", max_length=40)),
        custom=_all_runs,
    ),
    RestTool(
        name="qlik_get_automation_run", title="Get automation run", group="automations",
        description="Details of one automation run: status, start and stop time, context, and error.",
        method="GET", path="/api/v1/automations/{automation_id}/runs/{run_id}",
        params=(path_param("automation_id", "The automation id"), path_param("run_id", "The run id")),
        result=_shape_run,
    ),
    RestTool(
        name="qlik_fetch_automation_run", title="Wait for automation run", group="automations",
        description="Wait for an automation run to finish (up to timeout_seconds, default 30) and return its final state, so you do not have to poll.",
        params=(path_param("automation_id", "The automation id"), path_param("run_id", "The run id"),
                P("timeout_seconds", "How long to wait before returning the current state", int, default=30, ge=1, le=120)),
        custom=_fetch_run,
    ),
    RestTool(
        name="qlik_get_automation_run_log", title="Get automation run log", group="automations",
        description="Download the log of an automation run (block-by-block output and errors), truncated at max_chars. Qlik's 'run display' data is not public; the log is the closest equivalent.",
        params=(path_param("automation_id", "The automation id"), path_param("run_id", "The run id"),
                P("max_chars", "Maximum characters of log to return", int, default=20000, ge=500, le=200000)),
        custom=_run_log,
    ),
    RestTool(
        name="qlik_start_automation_run", title="Start automation run", group="automations", writes=True,
        description="Start a run of an automation via the API and return the queued run. Follow with qlik_fetch_automation_run to wait for the result.",
        method="POST", path="/api/v1/automations/{automation_id}/runs",
        params=(path_param("automation_id", "The automation id"),),
        body=lambda args: {"context": "api"}, result=_shape_run,
    ),
    RestTool(
        name="qlik_stop_automation_run", title="Stop automation run", group="automations", writes=True,
        description="Stop a running automation run so it does not execute any further blocks.",
        method="POST", path="/api/v1/automations/{automation_id}/runs/{run_id}/actions/stop",
        params=(path_param("automation_id", "The automation id"), path_param("run_id", "The run id")),
        body=lambda args: {}, result=lambda raw, args: {"automation_id": args["automation_id"], "run_id": args["run_id"], "stopped": True},
    ),
    RestTool(
        name="qlik_retry_automation_run", title="Retry automation run", group="automations", writes=True,
        description="Retry a failed automation run by creating a new run with the same inputs.",
        method="POST", path="/api/v1/automations/{automation_id}/runs/{run_id}/actions/retry",
        params=(path_param("automation_id", "The automation id"), path_param("run_id", "The run id")),
        body=lambda args: {}, result=_shape_run,
    ),
    RestTool(
        name="qlik_create_automation", title="Create automation", group="automations", writes=True,
        description="Create a new automation (empty unless a workspace block graph is given). The requesting account needs the automation creator role.",
        method="POST", path="/api/v1/automations",
        params=(P("name", "Automation name", required=True, where="body", max_length=256),
                P("space_id", "Space to create it in (omit for personal)", where="body", max_length=64),
                P("description", "Description", where="body", max_length=2000),
                P("max_concurrent_runs", "Maximum parallel runs", int, where="body", ge=1, le=100),
                P("schedules", "Schedules: list of {interval, startAt, stopAt, timezone}", list[dict], where="body"),
                P("workspace", "Workspace block graph as produced by the automation editor", dict, where="body")),
        result=lambda raw, args: _automation_detail(raw or {}, False),
    ),
    RestTool(
        name="qlik_update_automation", title="Update automation", group="automations", writes=True,
        description="Update an automation's name, description, schedules, concurrency, or workspace. Unspecified properties keep their current values.",
        params=(path_param("automation_id", "The automation id"),
                P("name", "New name", max_length=256), P("description", "New description", max_length=2000),
                P("max_concurrent_runs", "Maximum parallel runs", int, ge=1, le=100),
                P("schedules", "Replacement schedules: list of {interval, startAt, stopAt, timezone}", list[dict]),
                P("workspace", "Replacement workspace block graph", dict)),
        custom=_update_automation,
    ),
    RestTool(
        name="qlik_set_automation_enabled", title="Enable or disable automation", group="automations", writes=True,
        description="Enable or disable an automation (disabled automations do not run on schedule or trigger).",
        params=(path_param("automation_id", "The automation id"),
                P("enabled", "True to enable, false to disable", bool, required=True)),
        custom=_set_automation_enabled,
    ),
    RestTool(
        name="qlik_delete_automation", title="Delete automation", group="automations", writes=True,
        description="Permanently delete an automation and its run history.",
        method="DELETE", path="/api/v1/automations/{automation_id}",
        params=(path_param("automation_id", "The automation id"),),
        result=lambda raw, args: {"automation_id": args["automation_id"], "deleted": True},
    ),
    RestTool(
        name="qlik_list_automation_connections", title="List automation connections", group="automations",
        description="List the connections (authenticated links to external systems) available to automations, with connector and connected state.",
        method="GET", path="/api/v1/automation-connections",
        params=(P("filter", "SCIM filter over name, connectorId, ownerId, spaceId", max_length=512), p_limit(50, 200)),
        result=lambda raw, args: {"count": len(items_of(raw)), "connections": [
            pick(i, {"id": "id", "name": "name", "connector_id": "connectorId", "is_connected": "isConnected",
                     "space_id": "spaceId", "owner_id": "ownerId", "updated_at": "updatedAt"}) for i in items_of(raw)]},
        cache=True,
    ),
    RestTool(
        name="qlik_list_automation_connectors", title="List automation connectors", group="automations",
        description="List the connectors (integrations such as Slack, Salesforce, Qlik Cloud Services) available for building automations.",
        method="GET", path="/api/v1/automation-connectors",
        params=(P("filter", "SCIM filter on name, e.g. name co \"Slack\"", max_length=256), p_limit(100, 200)),
        result=lambda raw, args: {"count": len(items_of(raw)), "connectors": [
            pick(i, {"id": "id", "name": "name", "description": "description", "has_webhooks": "hasWebhooks",
                     "billable": "billable"}) for i in items_of(raw)]},
        cache=True,
    ),
    RestTool(
        name="qlik_get_automation_connector", title="Get automation connector", group="automations",
        description="Details of one automation connector by id or name, including whether it supports webhooks.",
        params=(P("connector", "Connector id or exact name", required=True, max_length=256),),
        custom=_get_connector,
    ),
)


# ══════════════════════════════════════════════════════════════════
# Business glossary
# ══════════════════════════════════════════════════════════════════

_TERM_KEYS = {
    "id": "id", "name": "name", "abbreviation": "abbreviation", "description": "description",
    "status": "status.type", "tags": "tags", "categories": "categories", "stewards": "stewards",
    "related_information": "relatedInformation", "glossary_id": "glossaryId", "updated_at": "updatedAt",
}
_GLOSSARY_KEYS = {"id": "id", "name": "name", "description": "description", "space_id": "spaceId",
                  "owner_id": "ownerId", "tags": "tags", "updated_at": "updatedAt"}


def _shape_terms(raw: Any, args: dict) -> dict:
    terms = [pick(t, _TERM_KEYS) for t in items_of(raw)]
    return {"glossary_id": args.get("glossary_id"), "count": len(terms), "terms": terms}


def _term_search_query(args: dict) -> dict:
    clauses = []
    if args.get("query"):
        q = _scim(args["query"])
        clauses.append(f'(name co {q} or description co {q} or abbreviation co {q})')
    if args.get("status"):
        clauses.append(f"status eq {_scim(args['status'])}")
    if args.get("category_id"):
        clauses.append(f"categories co {_scim(args['category_id'])}")
    return {"filter": " and ".join(clauses)} if clauses else {}


GLOSSARY_TOOLS = (
    RestTool(
        name="qlik_list_glossaries", title="List glossaries", group="glossary",
        description="List the business glossaries in the tenant.",
        method="GET", path="/api/v1/glossaries", params=(p_limit(50, 100),),
        result=lambda raw, args: {"count": len(items_of(raw)), "glossaries": [pick(g, _GLOSSARY_KEYS) for g in items_of(raw)]},
        cache=True,
    ),
    RestTool(
        name="qlik_get_glossary", title="Get glossary", group="glossary",
        description="Get a glossary's name, description, overview, and space.",
        method="GET", path="/api/v1/glossaries/{glossary_id}",
        params=(path_param("glossary_id", "The glossary id"),),
        result=lambda raw, args: pick(raw or {}, {**_GLOSSARY_KEYS, "overview": "overview"}),
    ),
    RestTool(
        name="qlik_create_glossary", title="Create glossary", group="glossary", writes=True,
        description="Create a new business glossary to hold governed term definitions and categories.",
        method="POST", path="/api/v1/glossaries",
        params=(P("name", "Glossary name", required=True, where="body", max_length=256),
                P("description", "Description", where="body", max_length=2000),
                P("overview", "Overview text shown on the glossary page", where="body", max_length=10000),
                P("space_id", "Space to create the glossary in", where="body", max_length=64),
                P("tags", "Tags", list[str], where="body")),
        result=lambda raw, args: pick(raw or {}, _GLOSSARY_KEYS),
    ),
    RestTool(
        name="qlik_get_full_glossary_export", title="Export full glossary", group="glossary",
        description="Export a whole glossary: every category and term with descriptions, stewards, relations, and links.",
        method="GET", path="/api/v1/glossaries/{glossary_id}/actions/export",
        params=(path_param("glossary_id", "The glossary id"),),
    ),
    RestTool(
        name="qlik_get_glossary_categories", title="List glossary categories", group="glossary",
        description="List the categories of a glossary (with parent ids for the hierarchy).",
        method="GET", path="/api/v1/glossaries/{glossary_id}/categories",
        params=(path_param("glossary_id", "The glossary id"), p_limit(100, 100)),
        result=lambda raw, args: {"glossary_id": args["glossary_id"], "count": len(items_of(raw)), "categories": [
            pick(c, {"id": "id", "name": "name", "description": "description", "parent_id": "parentId", "stewards": "stewards"})
            for c in items_of(raw)]},
    ),
    RestTool(
        name="qlik_create_glossary_category", title="Create glossary category", group="glossary", writes=True,
        description="Create a category in a glossary to organize terms.",
        method="POST", path="/api/v1/glossaries/{glossary_id}/categories",
        params=(path_param("glossary_id", "The glossary id"),
                P("name", "Category name", required=True, where="body", max_length=256),
                P("description", "Description", where="body", max_length=2000),
                P("parent_id", "Parent category id for nesting", where="body", max_length=64),
                P("stewards", "User ids of stewards", list[str], where="body")),
        result=lambda raw, args: pick(raw or {}, {"id": "id", "name": "name", "description": "description", "parent_id": "parentId"}),
    ),
    RestTool(
        name="qlik_search_glossary_terms", title="Search glossary terms", group="glossary",
        description="Find terms in a glossary by text (matched against name, description, abbreviation), optionally by status or category. Use this to look up the business definition of a metric before computing it.",
        method="GET", path="/api/v1/glossaries/{glossary_id}/terms",
        params=(path_param("glossary_id", "The glossary id"),
                P("query", "Text to search for", max_length=256),
                P("status", "Only terms with this status", enum=("draft", "verified", "deprecated")),
                P("category_id", "Only terms in this category", max_length=64),
                p_limit(25, 100)),
        query=_term_search_query, result=_shape_terms,
    ),
    RestTool(
        name="qlik_get_glossary_term", title="Get glossary term", group="glossary",
        description="Get one glossary term with its full definition, status, relations, and links to apps, datasets, fields, and master items.",
        method="GET", path="/api/v1/glossaries/{glossary_id}/terms/{term_id}",
        params=(path_param("glossary_id", "The glossary id"), path_param("term_id", "The term id")),
        result=lambda raw, args: {**pick(raw or {}, _TERM_KEYS), "links_to": (raw or {}).get("linksTo") or [],
                                  "relates_to": (raw or {}).get("relatesTo") or []},
    ),
    RestTool(
        name="qlik_create_glossary_term", title="Create glossary term", group="glossary", writes=True,
        description="Create a term in a glossary with its description, abbreviation, tags, categories, and stewards.",
        method="POST", path="/api/v1/glossaries/{glossary_id}/terms",
        params=(path_param("glossary_id", "The glossary id"),
                P("name", "Term name", required=True, where="body", max_length=256),
                P("description", "Business definition", where="body", max_length=10000),
                P("abbreviation", "Abbreviation", where="body", max_length=64),
                P("related_information", "Additional information", where="body", max_length=10000),
                P("tags", "Tags", list[str], where="body"),
                P("categories", "Category ids", list[str], where="body"),
                P("stewards", "User ids of stewards", list[str], where="body")),
        result=lambda raw, args: pick(raw or {}, _TERM_KEYS),
    ),
    RestTool(
        name="qlik_update_glossary_term", title="Update glossary term", group="glossary", writes=True,
        description="Update a term's name, description, abbreviation, tags, categories, stewards, or related information. Only the given properties change.",
        method="PATCH", path="/api/v1/glossaries/{glossary_id}/terms/{term_id}",
        params=(path_param("glossary_id", "The glossary id"), path_param("term_id", "The term id"),
                P("name", "New name", max_length=256), P("description", "New definition", max_length=10000),
                P("abbreviation", "New abbreviation", max_length=64),
                P("related_information", "New related information", max_length=10000),
                P("tags", "Replacement tags", list[str]), P("categories", "Replacement category ids", list[str]),
                P("stewards", "Replacement steward user ids", list[str])),
        body=lambda args: json_patch(args, {"name": "/name", "description": "/description", "abbreviation": "/abbreviation",
                                            "related_information": "/relatedInformation", "tags": "/tags",
                                            "categories": "/categories", "stewards": "/stewards"}),
        result=lambda raw, args: {"glossary_id": args["glossary_id"], "term_id": args["term_id"], "updated": True},
    ),
    RestTool(
        name="qlik_delete_glossary_term", title="Delete glossary term", group="glossary", writes=True,
        description="Permanently delete a glossary term and its links from its glossary.",
        method="DELETE", path="/api/v1/glossaries/{glossary_id}/terms/{term_id}",
        params=(path_param("glossary_id", "The glossary id"), path_param("term_id", "The term id")),
        result=lambda raw, args: {"glossary_id": args["glossary_id"], "term_id": args["term_id"], "deleted": True},
    ),
    RestTool(
        name="qlik_update_term_status", title="Update term status", group="glossary", writes=True,
        description="Change a glossary term's status between draft, verified, and deprecated.",
        method="POST", path="/api/v1/glossaries/{glossary_id}/terms/{term_id}/actions/change-status",
        params=(path_param("glossary_id", "The glossary id"), path_param("term_id", "The term id"),
                P("status", "New status", required=True, enum=("draft", "verified", "deprecated"))),
        body=lambda args: {}, result=lambda raw, args: pick(raw or {}, _TERM_KEYS),
    ),
    RestTool(
        name="qlik_get_glossary_term_links", title="Get term links", group="glossary",
        description="List the apps, datasets, fields, and master items linked to a glossary term.",
        method="GET", path="/api/v1/glossaries/{glossary_id}/terms/{term_id}/links",
        params=(path_param("glossary_id", "The glossary id"), path_param("term_id", "The term id"), p_limit(50, 100)),
        result=lambda raw, args: {"term_id": args["term_id"], "count": len(items_of(raw)), "links": [
            pick(link, {"id": "id", "type": "type", "resource_type": "resourceType", "resource_id": "resourceId",
                        "name": "name", "sub_resource_type": "subResourceType", "sub_resource_id": "subResourceId",
                        "sub_resource_name": "subResourceName", "open_url": "openUrl"}) for link in items_of(raw)]},
    ),
    RestTool(
        name="qlik_create_glossary_term_links", title="Link term to resource", group="glossary", writes=True,
        description="Link a glossary term to an app or dataset, optionally to a specific field, master dimension, or master measure inside it.",
        method="POST", path="/api/v1/glossaries/{glossary_id}/terms/{term_id}/links",
        params=(path_param("glossary_id", "The glossary id"), path_param("term_id", "The term id"),
                P("resource_type", "Type of the linked resource", required=True, enum=("app", "dataset")),
                P("resource_id", "Id of the app or dataset", required=True, max_length=64),
                P("link_type", "definition (this resource defines the term) or related", default="definition", enum=("definition", "related")),
                P("sub_resource_type", "Optional: link to a part of the resource", enum=("master_dimension", "master_measure", "field")),
                P("sub_resource_id", "Id of the master item (or field name)", max_length=256),
                P("sub_resource_name", "Display name of the sub resource", max_length=256)),
        body=lambda args: {k: v for k, v in {
            "resourceType": args["resource_type"], "resourceId": args["resource_id"], "type": args.get("link_type") or "definition",
            "subResourceType": args.get("sub_resource_type"), "subResourceId": args.get("sub_resource_id"),
            "subResourceName": args.get("sub_resource_name")}.items() if v is not None},
        result=lambda raw, args: {"term_id": args["term_id"], "link": pick(raw or {}, {"id": "id", "type": "type", "resource_id": "resourceId", "name": "name"}), "created": True},
    ),
)


# ══════════════════════════════════════════════════════════════════
# Datasets and data quality
# ══════════════════════════════════════════════════════════════════

_DATASET_KEYS = {
    "id": "id", "name": "name", "description": "description", "technical_name": "technicalName",
    "type": "type", "space_id": "spaceId", "owner_id": "ownerId", "qri": "qri", "tags": "tags",
    "created_time": "createdTime", "last_modified_time": "lastModifiedTime",
    "row_count": "operational.rowCount", "size_bytes": "operational.size",
    "last_load_time": "operational.lastLoadTime", "last_update_time": "operational.lastUpdateTime",
    "status": "operational.status", "data_asset_id": "dataAssetInfo.id", "data_store_id": "dataAssetInfo.dataStoreInfo.id",
}


def _schema_fields(raw: Any) -> list[dict]:
    schema = (raw or {}).get("schema") or {}
    return [
        pick(f, {"name": "name", "type": "dataType.type", "original_type": "dataType.originalType", "nullable": "nullable",
                 "primary_key": "primaryKey", "sensitive": "sensitive", "tags": "tags", "description": "description"})
        for f in schema.get("dataFields") or []
    ]


def _shape_dataset(raw: Any, args: dict) -> dict:
    out = pick(raw or {}, _DATASET_KEYS)
    out["field_count"] = len(((raw or {}).get("schema") or {}).get("dataFields") or [])
    return out


def _shape_profile(raw: Any, args: dict) -> dict:
    profiles = items_of(raw)
    if not profiles:
        return {"dataset_id": args["dataset_id"], "status": "no profile available", "tables": []}
    latest = profiles[0]
    meta = latest.get("meta") or {}
    tables = []
    for table in latest.get("profiles") or []:
        fields = []
        for f in table.get("fieldProfiles") or []:
            fields.append(pick(f, {
                "name": "name", "data_type": "dataType", "distinct_values": "distinctValueCount",
                "null_count": "nullValueCount", "empty_count": "emptyStringCount", "min": "minNumericValue",
                "max": "maxNumericValue", "average": "average", "median": "median", "std_dev": "standardDeviation",
                "min_length": "minStringLength", "max_length": "maxStringLength", "tags": "tags",
            }) | {"most_frequent": [pick(v, {"value": "value", "frequency": "frequency"}) for v in (f.get("mostFrequentValues") or [])[:5]],
                  "sample_values": (f.get("sampleValues") or [])[:5]})
        tables.append({"name": table.get("name"), "row_count": table.get("numberOfRows"), "fields": fields})
    return {"dataset_id": args["dataset_id"], "status": meta.get("status"), "computed_at": meta.get("computationEndTime"),
            "result_type": meta.get("resultType"), "tables": tables}


def _shape_sample(raw: Any, args: dict) -> dict:
    profiles = items_of(raw)
    max_rows = int(args.get("max_rows") or 10)
    samples = []
    for profile in profiles[:1]:
        for table in profile.get("samples") or []:
            rows = [r.get("values") or [] for r in (table.get("records") or [])[:max_rows]]
            samples.append({"table": table.get("name"), "columns": table.get("fieldNames") or [], "rows": rows})
    if not samples:
        return {"dataset_id": args["dataset_id"], "samples": [], "note": "No sample is stored for this dataset; run qlik_update_dataset_quality to compute a profile."}
    return {"dataset_id": args["dataset_id"], "samples": samples}


def _shape_trust(raw: Any, args: dict) -> dict:
    for item in items_of(raw):
        if item.get("datasetId") == args["dataset_id"] or len(items_of(raw)) == 1:
            return {"dataset_id": args["dataset_id"], "score": item.get("score"), "previous_score": item.get("previousScore"),
                    "updated_at": item.get("updatedAt"), "axes": item.get("axes") or []}
    return {"dataset_id": args["dataset_id"], "score": None, "note": "No trust score computed for this dataset yet."}


async def _dataset_memberships(ctx: ToolContext, args: dict) -> dict:
    dataset_id = args["dataset_id"]
    try:
        raw = await ctx.qlik_client.call("GET", "/api/v1/items", params={"resourceType": "dataproduct", "limit": 100})
    except QlikCloudError as e:
        return _error("qlik_get_dataset_memberships", e, dataset_id=dataset_id)
    products = items_of(raw)
    semaphore = asyncio.Semaphore(8)

    async def fetch(item: dict) -> Optional[dict]:
        async with semaphore:
            try:
                product = await ctx.qlik_client.call("GET", f"/api/data-governance/data-products/{item.get('resourceId')}")
            except QlikCloudError:
                return None
        if dataset_id in (product or {}).get("datasetIds", []):
            return pick(product, {"id": "id", "name": "name", "description": "description", "space_id": "spaceId",
                                  "activated": "activated", "owner_id": "ownerId"})
        return None

    found = [m for m in await asyncio.gather(*(fetch(p) for p in products)) if m]
    return {"dataset_id": dataset_id, "count": len(found), "data_products": found, "products_scanned": len(products)}


DATASET_TOOLS = (
    RestTool(
        name="qlik_get_dataset", title="Get dataset", group="datasets",
        description="Metadata of a dataset in the catalog: name, technical name, space, owner, row count, size, load times, and status.",
        method="GET", path="/api/v1/data-sets/{dataset_id}",
        params=(path_param("dataset_id", "The dataset id (from qlik_search with resource_type 'dataset', use resource_id)"),),
        result=_shape_dataset, cache=True,
    ),
    RestTool(
        name="qlik_get_dataset_schema", title="Get dataset schema", group="datasets",
        description="Column definitions of a dataset: names, data types, nullability, keys, sensitivity, and tags.",
        method="GET", path="/api/v1/data-sets/{dataset_id}",
        params=(path_param("dataset_id", "The dataset id"),),
        result=lambda raw, args: {"dataset_id": args["dataset_id"], "field_count": len(_schema_fields(raw)), "fields": _schema_fields(raw)},
        cache=True,
    ),
    RestTool(
        name="qlik_get_dataset_profile", title="Get dataset profile", group="datasets",
        description="Statistics per column of a dataset: distinct counts, nulls, min, max, average, most frequent values.",
        method="GET", path="/api/v1/data-sets/{dataset_id}/profiles",
        params=(path_param("dataset_id", "The dataset id"),),
        result=_shape_profile,
    ),
    RestTool(
        name="qlik_get_dataset_sample", title="Get dataset sample", group="datasets",
        description="Preview the first rows of a dataset from its stored profile sample.",
        method="GET", path="/api/v1/data-sets/{dataset_id}/profiles",
        params=(path_param("dataset_id", "The dataset id"),
                P("max_rows", "Rows to return per table", int, default=10, ge=1, le=100)),
        result=_shape_sample,
    ),
    RestTool(
        name="qlik_get_dataset_freshness", title="Get dataset freshness", group="datasets",
        description="When a dataset was last loaded and updated, and its current status.",
        method="GET", path="/api/v1/data-sets/{dataset_id}",
        params=(path_param("dataset_id", "The dataset id"),),
        result=lambda raw, args: {"dataset_id": args["dataset_id"], **pick(raw or {}, {
            "last_load_time": "operational.lastLoadTime", "last_update_time": "operational.lastUpdateTime",
            "content_updated": "operational.contentUpdated", "status": "operational.status",
            "row_count": "operational.rowCount", "last_modified_time": "lastModifiedTime"})},
    ),
    RestTool(
        name="qlik_get_dataset_trust_score", title="Get dataset trust score", group="datasets",
        description="Qlik's trust score for a dataset (0 to 5) with the axes that make it up: validity, completeness, discoverability, usage, and more.",
        method="POST", path="/api/data-governance/trust-scores/results/data-sets/actions/filter",
        params=(P("dataset_id", "The dataset id", required=True, where="local", max_length=64),),
        body=lambda args: {"datasetIds": [args["dataset_id"]]}, result=_shape_trust,
    ),
    RestTool(
        name="qlik_get_dataset_memberships", title="Get dataset data products", group="datasets",
        description="Which data products include a dataset (the dataset's memberships in the catalog).",
        params=(P("dataset_id", "The dataset id", required=True, where="local", max_length=64),),
        custom=_dataset_memberships,
    ),
    RestTool(
        name="qlik_update_dataset_metadata", title="Update dataset metadata", group="datasets", writes=True,
        description="Rename a dataset or change its description.",
        method="PATCH", path="/api/v1/data-sets/{dataset_id}",
        params=(path_param("dataset_id", "The dataset id"), P("name", "New name", max_length=255),
                P("description", "New description", max_length=1024)),
        body=lambda args: json_patch(args, {"name": "/name", "description": "/description"}),
        result=lambda raw, args: {"dataset_id": args["dataset_id"], "updated": True, **pick(raw or {}, {"name": "name", "description": "description"})},
    ),
    RestTool(
        name="qlik_update_dataset_quality", title="Compute dataset quality", group="datasets", writes=True,
        description="Start a data quality computation (profile plus validity checks) for a dataset. Returns a computation id to poll with qlik_get_dataset_quality_computation_status.",
        method="POST", path="/api/v1/data-qualities/computations",
        params=(P("dataset_id", "The dataset id", required=True, where="body", max_length=64),
                P("connection_id", "Data connection id when the dataset has several", where="body", max_length=64)),
        result=lambda raw, args: {"dataset_id": args["dataset_id"], "computation_id": (raw or {}).get("computationId"), "started": True},
    ),
    RestTool(
        name="qlik_get_dataset_quality_computation_status", title="Get quality computation status", group="datasets",
        description="Status of a data quality computation started with qlik_update_dataset_quality.",
        method="GET", path="/api/v1/data-qualities/computations/{computation_id}",
        params=(path_param("computation_id", "The computation id"),),
        result=lambda raw, args: {"computation_id": args["computation_id"], "status": (raw or {}).get("status")},
    ),
    RestTool(
        name="qlik_get_dataset_quality", title="Get dataset quality results", group="datasets",
        description="Global data quality results for a dataset: counts of valid, invalid, and empty values per field.",
        method="GET", path="/api/v1/data-qualities/global-results",
        params=(P("dataset_id", "The dataset id", required=True, max_length=64),
                P("connection_id", "Data connection id when the dataset has several", max_length=64)),
    ),
)


# ══════════════════════════════════════════════════════════════════
# Data products
# ══════════════════════════════════════════════════════════════════

_PRODUCT_KEYS = {
    "id": "id", "name": "name", "description": "description", "space_id": "spaceId", "owner_id": "ownerId",
    "activated": "activated", "activated_at": "activatedAt", "activated_on": "activatedOn", "dataset_ids": "datasetIds",
    "api_consumable_dataset_ids": "apiConsumableDatasetIds", "glossary_ids": "glossaryIds", "key_contacts": "keyContacts",
    "tags": "tags", "qri": "qri", "trust_score": "trustScore.score", "created_at": "createdAt", "updated_at": "updatedAt",
}


async def _list_data_products(ctx: ToolContext, args: dict) -> dict:
    params: dict = {"resourceType": "dataproduct", "limit": int(args.get("limit") or 50)}
    if args.get("query"):
        params["query"] = args["query"]
    if args.get("space_id"):
        params["spaceId"] = args["space_id"]
    try:
        raw = await ctx.qlik_client.call("GET", "/api/v1/items", params=params, cache=True)
    except QlikCloudError as e:
        return _error("qlik_list_data_products", e)
    products = [pick(i, {"id": "resourceId", "item_id": "id", "name": "name", "description": "description",
                         "space_id": "spaceId", "owner_id": "ownerId", "updated_at": "updatedAt"}) for i in items_of(raw)]
    return {"count": len(products), "data_products": products}


DATA_PRODUCT_TOOLS = (
    RestTool(
        name="qlik_list_data_products", title="List data products", group="data_products",
        description="List data products (governed, documented bundles of datasets) in the catalog, optionally filtered by text or space.",
        params=(P("query", "Text to match against name and description", max_length=256),
                P("space_id", "Only products in this space", max_length=64), p_limit(50, 100)),
        custom=_list_data_products,
    ),
    RestTool(
        name="qlik_get_data_product", title="Get data product", group="data_products",
        description="Details of a data product: datasets, glossaries, key contacts, activation state, tags, and trust score.",
        method="GET", path="/api/data-governance/data-products/{data_product_id}",
        params=(path_param("data_product_id", "The data product id"),),
        result=lambda raw, args: {**pick(raw or {}, _PRODUCT_KEYS), "read_me": ((raw or {}).get("readMe") or "")[:8000]},
    ),
    RestTool(
        name="qlik_get_data_product_documentation", title="Get data product documentation", group="data_products",
        description="The markdown documentation consumers see for a data product.",
        method="POST", path="/api/data-governance/data-products/{data_product_id}/actions/export-documentation",
        params=(path_param("data_product_id", "The data product id"),),
        body=lambda args: {}, text=True,
        result=lambda raw, args: {"data_product_id": args["data_product_id"], "markdown": raw if isinstance(raw, str) else str(raw)},
    ),
    RestTool(
        name="qlik_create_data_product", title="Create data product", group="data_products", writes=True,
        description="Create a data product from datasets, with description, README, glossaries, key contacts, and tags.",
        method="POST", path="/api/data-governance/data-products",
        params=(P("name", "Data product name", required=True, where="body", max_length=256),
                P("description", "Description", where="body", max_length=2000),
                P("space_id", "Space to create it in", where="body", max_length=64),
                P("dataset_ids", "Dataset ids to include", list[str], where="body"),
                P("api_consumable_dataset_ids", "Dataset ids exposed for API consumption", list[str], where="body"),
                P("glossary_ids", "Glossary ids documenting the product", list[str], where="body"),
                P("read_me", "README markdown", where="body", max_length=50000),
                P("tags", "Tags", list[str], where="body"),
                P("key_contacts", "Key contacts: list of {userId, role}", list[dict], where="body")),
        result=lambda raw, args: pick(raw or {}, _PRODUCT_KEYS),
    ),
    RestTool(
        name="qlik_update_data_product", title="Update data product", group="data_products", writes=True,
        description="Update a data product's name, description, datasets, glossaries, README, key contacts, or tags. Only the given properties change.",
        method="PATCH", path="/api/data-governance/data-products/{data_product_id}",
        params=(path_param("data_product_id", "The data product id"),
                P("name", "New name", max_length=256), P("description", "New description", max_length=2000),
                P("dataset_ids", "Replacement dataset ids", list[str]),
                P("api_consumable_dataset_ids", "Replacement API-consumable dataset ids", list[str]),
                P("glossary_ids", "Replacement glossary ids", list[str]), P("read_me", "New README markdown", max_length=50000),
                P("tags", "Replacement tags", list[str]), P("key_contacts", "Replacement key contacts", list[dict])),
        body=lambda args: json_patch(args, {"name": "/name", "description": "/description", "dataset_ids": "/datasetIds",
                                            "api_consumable_dataset_ids": "/apiConsumableDatasetIds", "glossary_ids": "/glossaryIds",
                                            "read_me": "/readMe", "tags": "/tags", "key_contacts": "/keyContacts"}),
        result=lambda raw, args: {"data_product_id": args["data_product_id"], "updated": True},
    ),
    RestTool(
        name="qlik_update_data_product_space", title="Move data product", group="data_products", writes=True,
        description="Move a data product to another space, which changes who can see and use it.",
        method="POST", path="/api/data-governance/data-products/{data_product_id}/actions/move",
        params=(path_param("data_product_id", "The data product id"), P("space_id", "Target space id", required=True, where="body", max_length=64)),
        result=lambda raw, args: {"data_product_id": args["data_product_id"], "space_id": args["space_id"], "moved": True},
    ),
    RestTool(
        name="qlik_update_activate_data_product", title="Activate data product", group="data_products", writes=True,
        description="Activate (publish) a data product so consumers can find and use it, optionally into a specific space and under a given name.",
        method="POST", path="/api/data-governance/data-products/{data_product_id}/actions/activate",
        params=(path_param("data_product_id", "The data product id"),
                P("name", "Name of the activated product", required=True, where="body", max_length=256),
                P("description", "Description", where="body", max_length=2000),
                P("space_id", "Space to activate into", where="body", max_length=64),
                P("tags", "Tags", list[str], where="body")),
        result=lambda raw, args: pick(raw or {}, _PRODUCT_KEYS),
    ),
    RestTool(
        name="qlik_update_deactivate_data_product", title="Deactivate data product", group="data_products", writes=True,
        description="Deactivate a data product so it is no longer available to consumers.",
        method="POST", path="/api/data-governance/data-products/{data_product_id}/actions/deactivate",
        params=(path_param("data_product_id", "The data product id"),),
        body=lambda args: {}, result=lambda raw, args: {"data_product_id": args["data_product_id"], "deactivated": True},
    ),
    RestTool(
        name="qlik_delete_data_product", title="Delete data product", group="data_products", writes=True,
        description="Permanently delete a data product (the datasets themselves are not deleted).",
        method="DELETE", path="/api/data-governance/data-products/{data_product_id}",
        params=(path_param("data_product_id", "The data product id"),),
        result=lambda raw, args: {"data_product_id": args["data_product_id"], "deleted": True},
    ),
)


# ══════════════════════════════════════════════════════════════════
# Lineage
# ══════════════════════════════════════════════════════════════════

def _graph_summary(raw: Any) -> dict:
    graphs = []
    if isinstance(raw, dict):
        if raw.get("graph"):
            graphs.append(raw["graph"])
        nested = raw.get("graphs")
        if isinstance(nested, dict):
            nested = nested.get("graphs")
        if isinstance(nested, list):
            graphs.extend(g for g in nested if isinstance(g, dict))
    nodes: dict[str, dict] = {}
    edges = []
    for graph in graphs:
        for qri, node in (graph.get("nodes") or {}).items():
            meta = (node or {}).get("metadata") or {}
            nodes[qri] = {"qri": qri, "label": (node or {}).get("label"), "type": meta.get("type"), "subtype": meta.get("subtype"),
                          "tables": meta.get("tables"), "fields": meta.get("fields")}
        for edge in graph.get("edges") or []:
            edges.append({"source": edge.get("source"), "target": edge.get("target"), "relation": edge.get("relation")})
    return {"node_count": len(nodes), "edge_count": len(edges), "nodes": list(nodes.values()), "edges": edges}


async def _lineage(ctx: ToolContext, args: dict) -> dict:
    qri = args.get("qri")
    try:
        if not qri and args.get("app_id"):
            qri = f"qri:app:sense://{args['app_id']}"
        if not qri and args.get("dataset_id"):
            dataset = await ctx.qlik_client.call("GET", f"/api/v1/data-sets/{args['dataset_id']}", cache=True)
            qri = (dataset or {}).get("qri")
        if not qri:
            return {"error": "Provide qri, app_id, or dataset_id", "tool": "qlik_get_lineage"}
        direction = args.get("direction") or "upstream"
        levels = int(args.get("levels") or 3)
        encoded = qri.replace("/", "%2F").replace(":", "%3A").replace("#", "%23")
        if direction == "downstream":
            raw = await ctx.qlik_client.call("GET", f"/api/v1/lineage-graphs/impact/{encoded}/overview", params={"down": levels})
        else:
            raw = await ctx.qlik_client.call(
                "GET", f"/api/v1/lineage-graphs/nodes/{encoded}",
                params={"up": levels, "level": args.get("level") or "resource", "collapse": True},
            )
    except QlikCloudError as e:
        return _error("qlik_get_lineage", e, qri=qri)
    return {"qri": qri, "direction": direction, "levels": levels, **_graph_summary(raw)}


LINEAGE_TOOLS = (
    RestTool(
        name="qlik_get_lineage", title="Get lineage", group="lineage",
        description="Trace where an app's or dataset's data comes from (upstream sources and transformations) or what depends on it (downstream impact). Give a QRI, an app_id, or a dataset_id.",
        params=(P("qri", "Qlik resource identifier, e.g. qri:app:sense://<appId>", max_length=512),
                P("app_id", "App id (alternative to qri)", max_length=64),
                P("dataset_id", "Dataset id (alternative to qri)", max_length=64),
                P("direction", "upstream (origins) or downstream (impact)", default="upstream", enum=("upstream", "downstream")),
                P("levels", "How many levels to traverse (-1 for unlimited)", int, default=3, ge=-1, le=20),
                P("level", "Granularity for upstream graphs", default="resource", enum=("resource", "table", "field", "all"))),
        custom=_lineage,
    ),
    RestTool(
        name="qlik_get_app_data_lineage", title="Get app load lineage", group="lineage",
        description="The data sources an app's load script reads and writes (connections, files, tables), from the app's own lineage metadata.",
        method="GET", path="/api/v1/apps/{app_id}/data/lineage",
        params=(path_param("app_id", "The Qlik Cloud app ID"),),
        result=lambda raw, args: {"app_id": args["app_id"], "count": len(raw if isinstance(raw, list) else []),
                                  "sources": [pick(i, {"discriminator": "discriminator", "statement": "statement"}) for i in (raw if isinstance(raw, list) else [])]},
        cache=True,
    ),
)


# ══════════════════════════════════════════════════════════════════
# Knowledge bases, pipelines, alerts, ML, reloads, spaces, answers
# ══════════════════════════════════════════════════════════════════

def _chunks(raw: Any) -> list:
    if isinstance(raw, dict) and isinstance(raw.get("chunks"), list):
        return raw["chunks"]
    return items_of(raw)


KNOWLEDGE_TOOLS = (
    RestTool(
        name="qlik_list_knowledgebases", title="List knowledge bases", group="knowledge",
        description="List the Qlik Answers knowledge bases (indexed document collections) the account can search.",
        method="GET", path="/api/v1/knowledgebases", params=(p_limit(50, 100),),
        result=lambda raw, args: {"count": len(items_of(raw)), "knowledgebases": [
            pick(k, {"id": "id", "name": "name", "description": "description", "space_id": "spaceId", "updated_at": "updatedAt"}) for k in items_of(raw)]},
        cache=True,
    ),
    RestTool(
        name="qlik_search_knowledgebase_chunks", title="Search knowledge base", group="knowledge",
        description="Semantic search over a knowledge base's indexed documents; returns the most relevant text chunks with their sources, for grounding answers.",
        method="POST", path="/api/v1/knowledgebases/{knowledgebase_id}/actions/search",
        params=(path_param("knowledgebase_id", "The knowledge base id"),
                P("query", "Question or search text", required=True, where="body", api_name="prompt", max_length=2000),
                P("top_n", "Number of chunks to return", int, default=5, where="body", api_name="topN", ge=1, le=50),
                P("search_mode", "SIMPLE (semantic) or FULL (semantic plus reranking)", default="SIMPLE", where="body", api_name="searchMode", enum=("SIMPLE", "FULL"))),
        result=lambda raw, args: {"knowledgebase_id": args["knowledgebase_id"], "chunks": [
            {"text": c.get("text"), "score": c.get("semanticScore"), "source": (c.get("chunkMeta") or {}).get("source"),
             "document_id": (c.get("chunkMeta") or {}).get("documentId")}
            for c in _chunks(raw)]},
    ),
)


async def _pipeline_details(ctx: ToolContext, args: dict) -> dict:
    project_id = args["project_id"]
    try:
        bindings = await ctx.qlik_client.call("GET", f"/api/v1/di-projects/{project_id}/bindings")
        tasks = await ctx.qlik_client.call("GET", f"/api/v1/di-projects/{project_id}/di-tasks")
    except QlikCloudError as e:
        return _error("qlik_get_pipeline_project_details", e, project_id=project_id)
    return {"project_id": project_id, "bindings": bindings, "tasks": [
        pick(t, {"id": "id", "name": "name", "type": "type", "state": "state", "space_id": "spaceId", "description": "description"}) or t
        for t in items_of(tasks)]}


PIPELINE_TOOLS = (
    RestTool(
        name="qlik_list_pipeline_projects", title="List data pipeline projects", group="pipelines",
        description="List Qlik Talend Cloud data integration (pipeline) projects, optionally by space.",
        method="GET", path="/api/v1/di-projects", params=(P("space_id", "Only projects in this space", max_length=64),),
        result=lambda raw, args: {"count": len(items_of(raw)), "projects": [
            pick(p, {"id": "id", "name": "name", "description": "description", "space_id": "spaceId", "type": "type", "platform": "platform"}) or p
            for p in items_of(raw)]},
        cache=True,
    ),
    RestTool(
        name="qlik_get_pipeline_project_details", title="Get pipeline project details", group="pipelines",
        description="A pipeline project's binding values (connections and variables) and its data tasks.",
        params=(path_param("project_id", "The pipeline project id"),), custom=_pipeline_details,
    ),
    RestTool(
        name="qlik_get_pipeline_task_state", title="Get pipeline task state", group="pipelines",
        description="Runtime state of one data task in a pipeline project.",
        method="GET", path="/api/v1/di-projects/{project_id}/di-tasks/{task_id}/runtime/state",
        params=(path_param("project_id", "The pipeline project id"), path_param("task_id", "The data task id")),
    ),
    RestTool(
        name="qlik_list_data_connections", title="List data connections", group="pipelines",
        description="List data connections (databases, cloud storage, files) defined in the tenant, with their type and space.",
        method="GET", path="/api/v1/data-connections",
        result=lambda raw, args: {"count": len(items_of(raw)), "connections": [
            pick(c, {"id": "id", "name": "qName", "type": "datasourceID", "connection_type": "qType", "space_id": "space",
                     "owner_id": "qOwnerId", "updated": "qUpdated"}) or c for c in items_of(raw)]},
        cache=True,
    ),
)

_ALERT_KEYS = {
    "id": "id", "name": "name", "description": "description", "app_id": "appId", "enabled": "enabled",
    "trigger_type": "triggerType", "status": "status", "owner_name": "ownerName", "last_trigger": "lastTrigger",
    "last_scan": "lastScan", "last_execution_status": "lastExecutionStatus", "condition_id": "conditionId",
    "bookmark_id": "bookmarkId", "sheet_id": "sheetId",
}

ALERT_TOOLS = (
    RestTool(
        name="qlik_list_data_alerts", title="List data alerts", group="alerts",
        description="List data alert tasks (conditions evaluated on reload or schedule that notify people), optionally for one app.",
        method="GET", path="/api/v1/data-alerts",
        params=(P("app_id", "Only alerts on this app", api_name="appID", max_length=64),
                P("owner_id", "Only alerts owned by this user", max_length=64), p_limit(50, 100)),
        result=lambda raw, args: {"count": len(items_of(raw)), "alerts": [pick(a, _ALERT_KEYS) for a in items_of(raw)]},
        cache=True,
    ),
    RestTool(
        name="qlik_get_data_alert", title="Get data alert", group="alerts",
        description="Details of a data alert task: condition, trigger type, recipients, throttling, and last results.",
        method="GET", path="/api/v1/data-alerts/{alert_id}",
        params=(path_param("alert_id", "The alert id"),),
        result=lambda raw, args: {**pick(raw or {}, _ALERT_KEYS), "recipients": (raw or {}).get("recipients"),
                                  "throttling": (raw or {}).get("throttling"), "errors": (raw or {}).get("alertingTaskErrors")},
    ),
    RestTool(
        name="qlik_list_data_alert_executions", title="List data alert executions", group="alerts",
        description="Execution history of a data alert: when it was evaluated and whether the condition was met.",
        method="GET", path="/api/v1/data-alerts/{alert_id}/executions",
        params=(path_param("alert_id", "The alert id"),
                P("include_evaluation", "Include evaluation result details", bool, default=False),
                P("condition_status", "FINISHED, FAILED, or ALL", enum=("FINISHED", "FAILED", "ALL"))),
        result=lambda raw, args: {"alert_id": args["alert_id"], "count": len(items_of(raw)), "executions": [
            pick(e, {"id": "id", "trigger_time": "triggerTime", "condition_status": "conditionStatus",
                     "evaluation_status": "executionEvaluationStatus", "evaluation_id": "evaluationId", "errors": "errors"}) for e in items_of(raw)]},
    ),
    RestTool(
        name="qlik_trigger_data_alert", title="Trigger data alert", group="alerts", writes=True,
        description="Manually evaluate a data alert now (and notify recipients if its condition is met).",
        method="POST", path="/api/v1/data-alerts/actions/trigger",
        params=(P("alert_id", "The alert id", required=True, where="body", api_name="alertingTaskID", max_length=64),),
        result=lambda raw, args: {"alert_id": args["alert_id"], "triggered": True, "workflow_id": (raw or {}).get("workflowID")},
    ),
)


def _ml_items(raw: Any, keys: dict) -> list[dict]:
    out = []
    for item in items_of(raw):
        attrs = item.get("attributes") if isinstance(item, dict) and "attributes" in item else item
        shaped = pick(attrs, keys)
        if isinstance(item, dict) and item.get("id"):
            shaped.setdefault("id", item["id"])
        out.append(shaped)
    return out


def _ml_one(raw: Any, keys: dict) -> dict:
    data = (raw or {}).get("data") if isinstance(raw, dict) else None
    if isinstance(data, dict):
        shaped = pick(data.get("attributes") or {}, keys)
        shaped.setdefault("id", data.get("id"))
        return shaped
    return pick(raw or {}, keys)


_EXPERIMENT_KEYS = {"id": "id", "name": "name", "description": "description", "space_id": "spaceId", "owner_id": "ownerId",
                    "created_at": "createdAt", "updated_at": "updatedAt"}
_MODEL_KEYS = {"id": "id", "name": "name", "algorithm": "algorithm", "status": "status", "experiment_version_id": "experimentVersionId",
               "metrics": "metrics", "created_at": "createdAt"}
_DEPLOYMENT_KEYS = {"id": "id", "name": "name", "description": "description", "model_id": "modelId", "space_id": "spaceId",
                    "enable_predictions": "enablePredictions", "deprecated": "deprecated", "updated_at": "updatedAt"}

ML_TOOLS = (
    RestTool(
        name="qlik_list_ml_experiments", title="List AutoML experiments", group="ml",
        description="List Qlik AutoML experiments (machine learning projects), optionally filtered (e.g. spaceId eq \"...\").",
        method="GET", path="/api/v1/ml/experiments",
        params=(P("filter", "Filter expression over ownerId, spaceId, modelId, deploymentId", max_length=512), p_limit(32, 100)),
        result=lambda raw, args: {"count": len(items_of(raw)), "experiments": _ml_items(raw, _EXPERIMENT_KEYS)}, cache=True,
    ),
    RestTool(
        name="qlik_get_ml_experiment", title="Get AutoML experiment", group="ml",
        description="Details of one AutoML experiment: name, description, space, owner, and timestamps.",
        method="GET", path="/api/v1/ml/experiments/{experiment_id}",
        params=(path_param("experiment_id", "The experiment id"),),
        result=lambda raw, args: _ml_one(raw, _EXPERIMENT_KEYS),
    ),
    RestTool(
        name="qlik_list_ml_experiment_models", title="List AutoML models", group="ml",
        description="Models trained in an AutoML experiment with their algorithm, status, and metrics.",
        method="GET", path="/api/v1/ml/experiments/{experiment_id}/models",
        params=(path_param("experiment_id", "The experiment id"),
                P("filter", "Filter, e.g. experimentVersionId eq \"...\"", max_length=512), p_limit(32, 100)),
        result=lambda raw, args: {"experiment_id": args["experiment_id"], "count": len(items_of(raw)), "models": _ml_items(raw, _MODEL_KEYS)},
    ),
    RestTool(
        name="qlik_list_ml_deployments", title="List AutoML deployments", group="ml",
        description="List deployed AutoML models available for predictions.",
        method="GET", path="/api/v1/ml/deployments",
        params=(P("filter", "Filter, e.g. nameContains \"churn\" or spaceId eq \"...\"", max_length=512), p_limit(32, 100)),
        result=lambda raw, args: {"count": len(items_of(raw)), "deployments": _ml_items(raw, _DEPLOYMENT_KEYS)}, cache=True,
    ),
    RestTool(
        name="qlik_get_ml_deployment", title="Get AutoML deployment", group="ml",
        description="Details of an AutoML deployment, including whether predictions are enabled.",
        method="GET", path="/api/v1/ml/deployments/{deployment_id}",
        params=(path_param("deployment_id", "The deployment id"),),
        result=lambda raw, args: _ml_one(raw, _DEPLOYMENT_KEYS),
    ),
    RestTool(
        name="qlik_run_ml_prediction", title="Run AutoML prediction", group="ml",
        description="Score rows in real time with a deployed AutoML model. Give the feature column names and the rows (as strings); optionally include SHAP explanations.",
        method="POST", path="/api/v1/ml/deployments/{deployment_id}/realtime-predictions/actions/run",
        params=(path_param("deployment_id", "The deployment id"),
                P("columns", "Feature column names, in the order used in rows", list[str], required=True, where="local"),
                P("rows", "Rows of feature values as strings, e.g. [['42', 'NY'], ['31', 'CA']]", list[list[str]], required=True, where="local"),
                P("include_shap", "Include SHAP feature contributions", bool, default=False, api_name="includeShap"),
                P("include_not_predicted_reason", "Explain rows that could not be scored", bool, default=False, api_name="includeNotPredictedReason")),
        body=lambda args: {"schema": [{"name": c} for c in args["columns"]], "rows": args["rows"]},
        result=lambda raw, args: {"deployment_id": args["deployment_id"], **(lambda attrs: {"columns": [s.get("name") for s in attrs.get("schema") or []], "rows": attrs.get("rows") or []})(((raw or {}).get("data") or {}).get("attributes") or {})},
    ),
)

_RELOAD_KEYS = {"id": "id", "app_id": "appId", "status": "status", "type": "type", "partial": "partial", "creation_time": "creationTime",
                "start_time": "startTime", "end_time": "endTime", "engine_time": "engineTime", "error_code": "errorCode",
                "error_message": "errorMessage", "user_id": "userId"}


async def _app_reload_log(ctx: ToolContext, args: dict) -> dict:
    app_id = args["app_id"]
    try:
        if args.get("reload_id"):
            text = await ctx.qlik_client.call("GET", f"/api/v1/apps/{app_id}/reloads/logs/{args['reload_id']}", text=True)
            text = text if isinstance(text, str) else str(text)
            limit = int(args.get("max_chars") or 20000)
            return {"app_id": app_id, "reload_id": args["reload_id"], "length": len(text), "log": text[-limit:]}
        raw = await ctx.qlik_client.call("GET", f"/api/v1/apps/{app_id}/reloads/logs")
    except QlikCloudError as e:
        return _error("qlik_get_app_reload_log", e, app_id=app_id)
    entries = items_of(raw)
    return {"app_id": app_id, "count": len(entries), "logs": [
        pick(e, {"reload_id": "reloadId", "log_id": "logId", "end_time": "endTime", "size": "size", "status": "status"}) or e
        for e in entries[:20]], "note": "Pass reload_id to read one log."}


RELOAD_TOOLS = (
    RestTool(
        name="qlik_list_reloads", title="List reloads", group="reloads",
        description="Reload history of an app: status, timing, and errors of recent reloads.",
        method="GET", path="/api/v1/reloads",
        params=(P("app_id", "The Qlik Cloud app ID", required=True, max_length=64), p_limit(20, 100)),
        result=lambda raw, args: {"app_id": args["app_id"], "count": len(items_of(raw)), "reloads": [pick(r, _RELOAD_KEYS) for r in items_of(raw)]},
    ),
    RestTool(
        name="qlik_get_reload", title="Get reload", group="reloads",
        description="Status, timing, and error details of one reload, by reload id.",
        method="GET", path="/api/v1/reloads/{reload_id}",
        params=(path_param("reload_id", "The reload id"),), result=lambda raw, args: pick(raw or {}, _RELOAD_KEYS),
    ),
    RestTool(
        name="qlik_start_reload", title="Start reload", group="reloads", writes=True,
        description="Queue a reload of an app (full, or partial to run only partial-load statements). Returns the reload id to check with qlik_get_reload.",
        method="POST", path="/api/v1/reloads",
        params=(P("app_id", "The Qlik Cloud app ID", required=True, where="body", max_length=64),
                P("partial", "Run a partial reload", bool, default=False, where="body")),
        result=lambda raw, args: pick(raw or {}, _RELOAD_KEYS),
    ),
    RestTool(
        name="qlik_cancel_reload", title="Cancel reload", group="reloads", writes=True,
        description="Cancel a queued or running reload of an app before it completes.",
        method="POST", path="/api/v1/reloads/{reload_id}/actions/cancel",
        params=(path_param("reload_id", "The reload id"),), body=lambda args: {},
        result=lambda raw, args: {"reload_id": args["reload_id"], "cancel_requested": True},
    ),
    RestTool(
        name="qlik_get_app_reload_log", title="Get reload log", group="reloads",
        description="List an app's reload logs, or read one log (pass reload_id) to diagnose a failed reload. Long logs return their tail.",
        params=(path_param("app_id", "The Qlik Cloud app ID"), P("reload_id", "Reload id to read", max_length=64),
                P("max_chars", "Maximum characters of log to return", int, default=20000, ge=500, le=200000)),
        custom=_app_reload_log,
    ),
)

SPACE_TOOLS = (
    RestTool(
        name="qlik_list_spaces", title="List spaces", group="spaces",
        description="List spaces (shared, managed, data) the account can access, optionally matching a name.",
        method="GET", path="/api/v1/spaces",
        params=(P("name", "Name to search for (wildcards on both sides)", max_length=256),
                P("type", "Space type", enum=("shared", "managed", "data")), p_limit(50, 100)),
        result=lambda raw, args: {"count": len(items_of(raw)), "spaces": [
            pick(s, {"id": "id", "name": "name", "type": "type", "description": "description", "owner_id": "ownerId"}) for s in items_of(raw)]},
        cache=True,
    ),
    RestTool(
        name="qlik_get_space", title="Get space", group="spaces",
        description="Details of one space: name, type, description, owner, and creation time.",
        method="GET", path="/api/v1/spaces/{space_id}",
        params=(path_param("space_id", "The space id"),),
        result=lambda raw, args: pick(raw or {}, {"id": "id", "name": "name", "type": "type", "description": "description", "owner_id": "ownerId", "created_at": "createdAt"}),
    ),
)


def _shape_answer(raw: Any, args: dict) -> dict:
    responses = []
    conv = (raw or {}).get("conversationalResponse") if isinstance(raw, dict) else None
    entries = conv if isinstance(conv, list) else ([conv] if isinstance(conv, dict) else [])
    drilldown = None
    for entry in entries:
        drilldown = drilldown or entry.get("drillDownURI")
        for part in entry.get("responses") or []:
            item: dict = {"type": part.get("type")}
            if part.get("narrative"):
                item["narrative"] = (part["narrative"] or {}).get("text")
            if part.get("sentence"):
                item["sentence"] = (part["sentence"] or {}).get("text")
            if part.get("followupSentence"):
                item["followup"] = part["followupSentence"]
            if part.get("errorMessage"):
                item["error"] = part["errorMessage"]
            if part.get("infoType"):
                item["info_type"] = part["infoType"]
                item["info_values"] = part.get("infoValues")
            responses.append(item)
    nlu = [pick(e, {"text": "text", "type": "typeName", "field": "filterFieldName", "filter": "filterText"})
           for e in ((raw or {}).get("nluInfo") or {}).get("elements") or []]
    return {"question": args["question"], "app_id": args["app_id"], "responses": responses,
            "understood_as": nlu, "open_in_insight_advisor": drilldown, "errors": (raw or {}).get("errors")}


ANSWER_TOOLS = (
    RestTool(
        name="qlik_ask_question", title="Ask Insight Advisor", group="answers",
        description="Ask a natural-language question about an app's data through Qlik Insight Advisor (Qlik's own analysis engine) and get narrative answers, follow-ups, and a link to the analysis. Good for 'what drives', 'trend of', 'compare' questions when you would rather not build the query yourself.",
        method="POST", path="/api/v1/questions/actions/ask",
        params=(P("app_id", "The Qlik Cloud app ID", required=True, where="local", max_length=64),
                P("question", "The question in plain language", required=True, where="local", max_length=1000),
                P("lang", "ISO-639-1 language of the question", default="en", where="local", max_length=8)),
        body=lambda args: {"text": args["question"], "app": {"id": args["app_id"]}, "lang": args.get("lang") or "en",
                           "disableNarrative": False, "enableVisualizations": False, "disableFollowups": False},
        result=_shape_answer,
    ),
)


REST_TOOLS: tuple[RestTool, ...] = (
    AUTOMATION_TOOLS + GLOSSARY_TOOLS + DATASET_TOOLS + DATA_PRODUCT_TOOLS + LINEAGE_TOOLS
    + KNOWLEDGE_TOOLS + PIPELINE_TOOLS + ALERT_TOOLS + ML_TOOLS + RELOAD_TOOLS + SPACE_TOOLS + ANSWER_TOOLS
)

REST_TOOL_SPECS: tuple[ToolSpec, ...] = tuple(spec_for(tool) for tool in REST_TOOLS)
