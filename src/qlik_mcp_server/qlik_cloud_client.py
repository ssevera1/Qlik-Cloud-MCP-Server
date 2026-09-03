"""Qlik Cloud REST API client.

Handles catalog search, app metadata, and the generic ``call`` used by the
declarative REST tools. Reference: https://qlik.dev/apis/rest/
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .auth import AuthManager
from .config import Config

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# The Items API caps page size at 100.
_ITEMS_MAX_LIMIT = 100
# Never honor a Retry-After longer than this (seconds).
_MAX_RETRY_AFTER = 60

logger = logging.getLogger(__name__)


class QlikCloudError(Exception):
    """Raised when a Qlik Cloud REST API call fails."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


def json_dumps_stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class QlikCloudClient:
    """Async client for the Qlik Cloud REST API.

    One pooled HTTP client is kept for the life of the server, and read-only
    metadata calls that opt in are cached for ``qlik.cache_ttl_seconds``.
    """

    def __init__(
        self,
        config: Config,
        auth: AuthManager,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.config = config
        self.auth = auth
        self.base_url = config.qlik.tenant_url.rstrip("/")
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._cache: dict[tuple[str, str], tuple[float, Any]] = {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.config.qlik.timeout_seconds,
                follow_redirects=True,
                transport=self._transport,
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self, method: str, path: str, params: Optional[dict] = None,
        json_data: Optional[Any] = None,
    ) -> Any:
        """Make an authenticated request to the Qlik Cloud REST API."""
        client = await self._ensure_client()
        headers = await self.auth.get_rest_headers()
        url = f"{self.base_url}{path}"

        for attempt in range(self.config.qlik.max_retries):
            try:
                response = await client.request(
                    method, url, headers=headers, params=params, json=json_data,
                )

                if response.status_code == 429:
                    if attempt >= self.config.qlik.max_retries - 1:
                        raise QlikCloudError(
                            "Qlik Cloud rate limit exceeded; try again later",
                            status_code=429,
                        )
                    try:
                        retry_after = int(response.headers.get("Retry-After", "5"))
                    except ValueError:
                        retry_after = 5
                    retry_after = min(max(retry_after, 1), _MAX_RETRY_AFTER)
                    logger.warning("Rate limited. Retrying in %ds...", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 400:
                    logger.debug(
                        "Qlik Cloud API error response (%d): %s",
                        response.status_code, response.text[:500],
                    )
                    raise QlikCloudError(
                        f"Qlik Cloud API request failed (HTTP {response.status_code})",
                        status_code=response.status_code,
                    )

                if response.content:
                    try:
                        return response.json()
                    except (json.JSONDecodeError, ValueError):
                        return {"raw_content": response.text}
                return None

            except httpx.TimeoutException as e:
                if attempt < self.config.qlik.max_retries - 1:
                    logger.warning(
                        "Request timeout, retrying (%d/%d)...",
                        attempt + 1, self.config.qlik.max_retries,
                    )
                    continue
                raise QlikCloudError("Request timed out after all retries") from e
            except httpx.HTTPError as e:
                # Transport-level failure (DNS, TLS, connection refused). Keep the
                # message generic for the agent; details go to the log.
                logger.error("Qlik Cloud request to %s failed: %s", path, e)
                raise QlikCloudError(
                    f"Could not reach Qlik Cloud tenant {self.config.tenant_host}: "
                    "connection failed"
                ) from e

        raise QlikCloudError("Max retries exceeded")

    async def call(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json: Optional[Any] = None,
        text: bool = False,
        cache: bool = False,
    ) -> Any:
        """Generic authenticated call used by the declarative REST tools.

        ``text`` returns the body as a string (for markdown or log exports).
        ``cache`` serves repeated GETs from memory for ``qlik.cache_ttl_seconds``.
        """
        ttl = self.config.qlik.cache_ttl_seconds
        cache_key: Optional[tuple[str, str]] = None
        if cache and method.upper() == "GET" and ttl > 0:
            cache_key = (path, json_dumps_stable(params))
            hit = self._cache.get(cache_key)
            if hit is not None and hit[0] > time.monotonic():
                return hit[1]

        raw = await self._request(method.upper(), path, params=params, json_data=json)
        if text:
            if isinstance(raw, dict) and set(raw) == {"raw_content"}:
                raw = raw["raw_content"]
            elif not isinstance(raw, str):
                raw = json_dumps_stable(raw)

        if cache_key is not None:
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[cache_key] = (time.monotonic() + ttl, raw)
        return raw

    async def fetch_text_url(self, url: str, max_chars: int = 20000) -> str:
        """Download a plain-text resource from an https URL returned by the API (no credentials sent)."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise QlikCloudError("Refusing to download from a non-https URL")
        try:
            async with httpx.AsyncClient(
                timeout=self.config.qlik.timeout_seconds, follow_redirects=True, transport=self._transport,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as e:
            raise QlikCloudError("Could not download the exported file") from e
        if response.status_code >= 400:
            raise QlikCloudError(f"Download failed (HTTP {response.status_code})", status_code=response.status_code)
        return response.text[:max_chars]

    def _open_url(self, item: dict) -> str:
        """Best-effort link to open an item in the Qlik Cloud hub."""
        href = ((item.get("links") or {}).get("open") or {}).get("href")
        if href:
            return href
        if item.get("resourceType") == "app" and item.get("resourceId"):
            return f"{self.base_url}/sense/app/{item['resourceId']}"
        return ""

    async def search_items(
        self,
        query: str,
        resource_type: Optional[str] = None,
        space_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search the Qlik Cloud catalog for apps, data products, and other items.

        Args:
            query: Search text (case-insensitive match on name or description).
            resource_type: Filter by type ("app", "dataset", "dataproduct", ...).
            space_id: Filter by space ID.
            limit: Maximum results to return (capped at 100 by the API).
        """
        params: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(limit, _ITEMS_MAX_LIMIT)),
        }
        if resource_type:
            params["resourceType"] = resource_type
        if space_id:
            params["spaceId"] = space_id

        result = await self._request("GET", "/api/v1/items", params=params)
        items = result.get("data", []) if result else []

        return [
            {
                "id": item.get("id", ""),
                "resource_id": item.get("resourceId", ""),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "resource_type": item.get("resourceType", ""),
                "resource_sub_type": item.get("resourceSubType", ""),
                "space_id": item.get("spaceId", ""),
                "owner_id": item.get("ownerId", ""),
                "updated_at": item.get("updatedAt", ""),
                "created_at": item.get("createdAt", ""),
                "url": self._open_url(item),
            }
            for item in items
        ]

    async def get_app(self, app_id: str) -> dict:
        """Get metadata for a specific app."""
        if not _UUID_RE.fullmatch(app_id):
            raise QlikCloudError("Invalid app_id: expected UUID format")
        result = await self.call("GET", f"/api/v1/apps/{app_id}", cache=True)
        if not result:
            raise QlikCloudError(f"App not found: {app_id}", status_code=404)
        return result.get("attributes", result)

    async def get_app_data_metadata(self, app_id: str) -> dict:
        """Data model metadata for an app: fields, tables, reload statistics."""
        if not _UUID_RE.fullmatch(app_id):
            raise QlikCloudError("Invalid app_id: expected UUID format")
        result = await self.call("GET", f"/api/v1/apps/{app_id}/data/metadata", cache=True)
        return result if isinstance(result, dict) else {}

    async def list_apps(
        self, space_id: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        """List apps, optionally filtered by space."""
        params: dict[str, Any] = {
            "limit": max(1, min(limit, _ITEMS_MAX_LIMIT)),
            "resourceType": "app",
        }
        if space_id:
            params["spaceId"] = space_id

        result = await self._request("GET", "/api/v1/items", params=params)
        return result.get("data", []) if result else []

    async def get_spaces(self) -> list[dict]:
        """List all accessible spaces."""
        result = await self._request("GET", "/api/v1/spaces")
        spaces = result.get("data", []) if result else []
        return [
            {
                "id": s.get("id", ""),
                "name": s.get("name", ""),
                "type": s.get("type", ""),
                "description": s.get("description", ""),
                "owner_id": s.get("ownerId", ""),
            }
            for s in spaces
        ]
