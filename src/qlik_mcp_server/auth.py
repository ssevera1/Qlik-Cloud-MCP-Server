"""Authentication module for Qlik Cloud API access.

Supports API key (bearer token) and OAuth2 M2M (client credentials grant).
Reference: https://qlik.dev/authenticate/oauth/getting-started-oauth-m2m/
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

from .config import Config

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Raised when authentication fails."""


class AuthManager:
    """Manages authentication credentials for Qlik Cloud APIs."""

    def __init__(
        self, config: Config, transport: Optional[httpx.AsyncBaseTransport] = None
    ) -> None:
        self.config = config
        self._transport = transport
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
        return {"Authorization": f"Bearer {token}"}

    async def _get_token(self) -> str:
        """Get a valid access token, refreshing if necessary."""
        if self.config.auth_mode == "api_key":
            return self.config.qlik.api_key

        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        return await self._refresh_oauth_token()

    def _token_url(self) -> str:
        oauth = self.config.qlik.oauth
        token_url = (oauth.token_url if oauth else "") or f"{self.config.qlik.tenant_url}/oauth/token"

        # The token endpoint must live on the tenant to prevent credential leakage / SSRF.
        tenant_host = self.config.tenant_host
        parsed = urlparse(token_url)
        if parsed.scheme != "https":
            raise AuthError("token_url must use HTTPS")
        if not parsed.hostname or (
            parsed.hostname != tenant_host
            and not parsed.hostname.endswith("." + tenant_host)
        ):
            raise AuthError("token_url must be under the configured tenant domain")
        return token_url

    async def _refresh_oauth_token(self) -> str:
        """Acquire a new OAuth2 access token via client credentials grant."""
        oauth = self.config.qlik.oauth
        if not oauth:
            raise AuthError("OAuth configuration is missing")

        token_url = self._token_url()
        logger.debug("Refreshing OAuth2 token from %s", token_url)

        async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            response = await client.post(
                token_url,
                json={
                    "client_id": oauth.client_id,
                    "client_secret": oauth.client_secret,
                    "grant_type": "client_credentials",
                },
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )

            if response.status_code != 200:
                logger.debug(
                    "OAuth2 token response (%d): %s",
                    response.status_code, response.text[:500],
                )
                raise AuthError(
                    f"OAuth2 token request failed (HTTP {response.status_code})"
                )

            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError) as e:
                raise AuthError("OAuth2 response is not valid JSON") from e

        self._access_token = data.get("access_token")
        if not self._access_token:
            raise AuthError("OAuth2 response did not contain an access_token")
        expires_in = data.get("expires_in", 3600)
        self._token_expiry = time.time() + expires_in

        logger.info("OAuth2 token acquired (expires in %ds)", expires_in)
        return self._access_token
