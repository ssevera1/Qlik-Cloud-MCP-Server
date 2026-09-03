"""A small declarative layer for tools that wrap one Qlik Cloud REST call.

Most catalog, governance, automation, and AI tools are a single HTTP request
plus some response shaping. Describing them as data (``RestTool``) keeps the
catalog compact, generates their input schemas, and lets one executor and one
contract test cover all of them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Optional
from urllib.parse import quote

from pydantic import BaseModel, Field, create_model

from ..qlik_cloud_client import QlikCloudError
from .spec import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

_UUID_LIKE = re.compile(r"^[A-Za-z0-9._:@+~-]{1,200}$")


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


@dataclass(frozen=True)
class P:
    """One tool parameter and where it goes in the HTTP request."""

    name: str
    description: str
    type: Any = str
    required: bool = False
    default: Any = None
    where: str = "query"  # path | query | body | local (handled by a custom builder)
    api_name: Optional[str] = None
    ge: Optional[int] = None
    le: Optional[int] = None
    max_length: Optional[int] = None
    enum: Optional[tuple[str, ...]] = None

    @property
    def wire_name(self) -> str:
        return self.api_name or camel(self.name)


@dataclass(frozen=True)
class RestTool:
    """A tool implemented as one REST call (or a custom coroutine)."""

    name: str
    title: str
    description: str
    method: str = "GET"
    path: str = ""
    params: tuple[P, ...] = ()
    group: str = "rest"
    writes: bool = False
    text: bool = False
    cache: bool = False
    body: Optional[Callable[[dict], Any]] = None
    query: Optional[Callable[[dict], dict]] = None
    result: Optional[Callable[[Any, dict], dict]] = None
    custom: Optional[Callable[[ToolContext, dict], Awaitable[dict]]] = None


# Common parameters
def p_limit(default: int = 20, le: int = 100, description: str = "Maximum number of items to return") -> P:
    return P("limit", f"{description} (default {default}, max {le})", int, default=default, ge=1, le=le)


def path_param(name: str, description: str) -> P:
    return P(name, description, str, required=True, where="path", max_length=200)


def build_input_model(tool: RestTool) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for param in tool.params:
        py_type: Any = param.type
        if param.enum:
            py_type = Literal[param.enum]  # type: ignore[valid-type]
        kwargs: dict[str, Any] = {"description": param.description}
        if param.ge is not None:
            kwargs["ge"] = param.ge
        if param.le is not None:
            kwargs["le"] = param.le
        if param.max_length is not None:
            kwargs["max_length"] = param.max_length
        if param.required:
            fields[param.name] = (py_type, Field(**kwargs))
        else:
            fields[param.name] = (Optional[py_type], Field(default=param.default, **kwargs))
    model_name = "".join(part.capitalize() for part in tool.name.split("_")) + "Input"
    return create_model(model_name, **fields)  # type: ignore[call-overload]


def _shape_default(raw: Any) -> dict:
    """A sane default shape: list responses become items + next cursor, others pass through."""
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            shaped: dict = {"count": len(data), "items": data}
            links = raw.get("links") or {}
            next_link = links.get("next") if isinstance(links, dict) else None
            if isinstance(next_link, dict) and next_link.get("href"):
                shaped["next_page"] = next_link["href"]
            for key in ("meta", "total", "pages", "page"):
                if key in raw:
                    shaped[key] = raw[key]
            return shaped
        return raw
    if isinstance(raw, list):
        return {"count": len(raw), "items": raw}
    if isinstance(raw, str):
        return {"text": raw}
    return {"result": raw}


def _fill_path(template: str, args: dict, params: tuple[P, ...]) -> str:
    path = template
    for param in params:
        if param.where != "path":
            continue
        value = str(args.get(param.name, ""))
        if not value or not _UUID_LIKE.match(value):
            raise QlikCloudError(f"Invalid {param.name}: unexpected characters")
        path = path.replace("{" + param.name + "}", quote(value, safe=""))
    if "{" in path:
        raise QlikCloudError(f"Unresolved path parameter in {template}")
    return path


async def run_rest_tool(tool: RestTool, ctx: ToolContext, args: dict) -> dict:
    """Execute a declarative REST tool."""
    if tool.custom is not None:
        return await tool.custom(ctx, args)

    path = _fill_path(tool.path, args, tool.params)

    query: dict[str, Any] = {}
    body_fields: dict[str, Any] = {}
    for param in tool.params:
        value = args.get(param.name)
        if value is None or (value is False and param.type is bool):
            # Absent booleans read as false on every Qlik endpoint; sending
            # "false" only adds noise (and breaks endpoints that reject unknowns).
            continue
        if param.where == "query":
            query[param.wire_name] = value
        elif param.where == "body":
            body_fields[param.wire_name] = value
    query_builder = tool.query
    if query_builder is not None:
        query.update(query_builder(args))

    body_builder = tool.body
    body: Any
    if body_builder is not None:
        body = body_builder(args)
    elif body_fields:
        body = body_fields
    else:
        body = None

    try:
        raw = await ctx.qlik_client.call(
            tool.method, path, params=query or None, json=body, text=tool.text, cache=tool.cache,
        )
    except QlikCloudError as e:
        logger.error("REST tool %s failed: %s", tool.name, e)
        return {"error": str(e), "tool": tool.name, "hint": _hint_for(e)}

    if tool.result is not None:
        return tool.result(raw, args)
    return _shape_default(raw)


def _hint_for(e: QlikCloudError) -> str:
    if e.status_code == 403:
        return "The service account lacks permission for this resource (check roles, scopes, and space membership)."
    if e.status_code == 404:
        return "Not found: check the id, or that the feature is enabled on this tenant."
    if e.status_code == 400:
        return "The request was rejected; check parameter values and formats."
    return "Verify the tenant URL, credentials, and that this API is available on the tenant."


def spec_for(tool: RestTool) -> ToolSpec:
    async def run(ctx: ToolContext, args: dict) -> dict:
        return await run_rest_tool(tool, ctx, args)

    return ToolSpec(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        input_model=build_input_model(tool),
        run=run,
        writes=tool.writes,
        group=tool.group,
    )


def json_patch(args: dict, fields: dict[str, str]) -> list[dict]:
    """Build a JSON Patch replacing each provided argument at its path."""
    ops = []
    for arg_name, pointer in fields.items():
        if args.get(arg_name) is not None:
            ops.append({"op": "replace", "path": pointer, "value": args[arg_name]})
    if not ops:
        raise QlikCloudError("Nothing to update: provide at least one property to change")
    return ops


def pick(item: Any, keys: dict[str, str]) -> dict:
    """Project an API object onto snake_case keys, skipping missing values."""
    if not isinstance(item, dict):
        return {}
    out: dict = {}
    for out_key, in_key in keys.items():
        value: Any = item
        for part in in_key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None:
            out[out_key] = value
    return out


def items_of(raw: Any) -> list:
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return data
        return []
    if isinstance(raw, list):
        return raw
    return []
