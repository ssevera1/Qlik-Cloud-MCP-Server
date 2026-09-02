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
    """One MCP tool: its schema (a Pydantic model), description, and handler."""

    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[ToolContext, dict], Awaitable[dict]]
    writes: bool = False
