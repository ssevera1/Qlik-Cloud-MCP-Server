"""Authentication module for Qlik Cloud API access.

Supports API key (bearer token) and OAuth2 M2M (client credentials grant).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from .config import Config

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when authentication fails."""


class AuthManager:
    """Manages authentication credentials for Qlik Cloud APIs."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0

    async def get_rest_headers(self) -> dict[str, str]:
        """Get HTTP headers with authentication for REST API calls."""
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_ws_headers(self) -> dict[str, str]:
        """Get headers for WebSocket connections to the Engine API."""
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
        }

    async def _get_token(self) -> str:
        """Get a valid access token, refreshing if necessary."""
        if self.config.auth_mode == "api_key":
            return self.config.qlik.api_key

        # OAuth2 M2M — check if cached token is still valid
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        return await self._refresh_oauth_token()

    async def _refresh_oauth_token(self) -> str:
        """Acquire a new OAuth2 access token via client credentials grant."""
        oauth = self.config.qlik.oauth
        if not oauth:
            raise AuthError("OAuth configuration is missing")

        token_url = oauth.token_url
        if not token_url:
            token_url = f"{self.config.qlik.tenant_url}/oauth/token"

        logger.debug("Refreshing OAuth2 token from %s", token_url)

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": oauth.client_id,
                    "client_secret": oauth.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                raise AuthError(
                    f"OAuth2 token request failed ({response.status_code}): "
                    f"{response.text}"
                )

            data = response.json()

        self._access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        self._token_expiry = time.time() + expires_in

        logger.info("OAuth2 token acquired (expires in %ds)", expires_in)
        return self._access_token
