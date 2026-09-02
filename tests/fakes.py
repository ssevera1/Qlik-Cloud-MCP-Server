"""Test doubles shared across test modules."""

from __future__ import annotations

import json
from typing import Any, Callable


class FakeWebSocket:
    """In-memory stand-in for a websockets connection to the Qlik Engine.

    ``responder`` receives the parsed JSON-RPC request and returns the
    ``result`` payload (or ``{"error": {...}}`` to simulate an engine error).
    Every sent request is recorded in ``sent`` so tests can assert on the
    exact wire format.
    """

    def __init__(self, responder: Callable[[dict], Any]) -> None:
        self.responder = responder
        self.sent: list[dict] = []
        self._queue: list[str] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        result = self.responder(msg)
        if isinstance(result, dict) and "error" in result and "result" not in result:
            payload = {"jsonrpc": "2.0", "id": msg["id"], "error": result["error"]}
        else:
            payload = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        self._queue.append(json.dumps(payload))

    def push_notification(self, method: str = "OnConnected", params: Any = None) -> None:
        """Queue an id-less engine notification ahead of pending responses."""
        self._queue.insert(
            0, json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})
        )

    async def recv(self) -> str:
        if not self._queue:
            raise AssertionError("recv() called with no queued message")
        return self._queue.pop(0)

    async def close(self) -> None:
        self.closed = True

    def calls(self, method: str) -> list[dict]:
        return [m for m in self.sent if m["method"] == method]
