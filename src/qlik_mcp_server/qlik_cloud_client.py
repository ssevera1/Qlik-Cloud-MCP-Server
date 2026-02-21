"""Qlik Cloud REST API client.

Handles app catalog queries, search, metadata retrieval,
and space listing via the Qlik Cloud REST API.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .auth import AuthManager
from .config import Config

logger = logging.getLogger(__name__)


class QlikCloudError(Exception):
    """Raised when a Qlik Cloud REST API call fails."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class QlikCloudClient:
    """Async client for the Qlik Cloud REST API."""

    def __init__(self, config: Config, auth: AuthManager) -> None:
        self.config = config
        self.auth = auth
        self.base_url = config.qlik.tenant_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.config.qlik.timeout_seconds,
                follow_redirects=True,
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
                    retry_after = int(response.headers.get("Retry-After", "5"))
                    logger.warning("Rate limited. Retrying in %ds...", retry_after)
                    import asyncio
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 400:
                    raise QlikCloudError(
                        f"Qlik Cloud API error: {response.status_code} - {response.text}",
                        status_code=response.status_code,
                    )

                if response.content:
                    return response.json()
                return None

            except httpx.TimeoutException:
                if attempt < self.config.qlik.max_retries - 1:
                    logger.warning("Request timeout, retrying (%d/%d)...", attempt + 1, self.config.qlik.max_retries)
                    continue
                raise QlikCloudError("Request timed out after all retries")

        raise QlikCloudError("Max retries exceeded")

    async def search_items(
        self,
        query: str,
        resource_type: Optional[str] = None,
        space_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search the Qlik Cloud catalog for apps, data products, and other items.

        Args:
            query: Search text (matches name, description, tags).
            resource_type: Filter by type ("app", "dataset", "automation", etc.).
            space_id: Filter by space ID.
            limit: Maximum results to return.

        Returns:
            List of matching items with metadata.
        """
        params: dict[str, Any] = {
            "query": query,
            "limit": min(limit, 40),
        }
        if resource_type:
            params["resourceType"] = resource_type
        if space_id:
            params["spaceId"] = space_id

        result = await self._request("GET", "/api/v1/items", params=params)
        items = result.get("data", []) if result else []

        # Flatten to essential fields
        return [
            {
                "id": item.get("id", ""),
                "resource_id": item.get("resourceId", ""),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "resource_type": item.get("resourceType", ""),
                "space_id": item.get("spaceId", ""),
                "owner_id": item.get("ownerId", ""),
                "updated_at": item.get("updatedAt", ""),
                "created_at": item.get("createdAt", ""),
                "collection_ids": item.get("collectionIds", []),
            }
            for item in items
        ]

    async def get_app(self, app_id: str) -> dict:
        """Get metadata for a specific app."""
        result = await self._request("GET", f"/api/v1/apps/{app_id}")
        if not result:
            raise QlikCloudError(f"App not found: {app_id}", status_code=404)
        return result.get("attributes", result)

    async def list_apps(
        self, space_id: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        """List apps, optionally filtered by space."""
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if space_id:
            params["spaceId"] = space_id

        result = await self._request("GET", "/api/v1/items", params={
            **params,
            "resourceType": "app",
        })
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

    async def get_app_objects(self, app_id: str) -> list[dict]:
        """Get objects (sheets, bookmarks) for an app via REST."""
        result = await self._request(
            "GET", f"/api/v1/apps/{app_id}/objects",
            params={"limit": 100},
        )
        return result.get("data", []) if result else []
