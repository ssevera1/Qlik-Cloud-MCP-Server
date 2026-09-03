"""Tool registration metadata shared by every tool module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel


@dataclass
class ToolContext:
    """What a tool handler needs at call time."""

    config: Any
    qlik_client: Any
    engine: Any


@dataclass(frozen=True)
class ToolSpec:
    """One MCP tool: its schema (a Pydantic model), description, and handler.

    ``writes`` marks tools that change persisted content in Qlik Cloud (they
    are hidden when ``tools.allow_writes`` is false). ``stateful`` marks tools
    that only change the engine session's selection state; they are neither
    read-only nor persisted writes.
    """

    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[ToolContext, dict], Awaitable[dict]]
    writes: bool = False
    stateful: bool = False
    group: str = "general"
