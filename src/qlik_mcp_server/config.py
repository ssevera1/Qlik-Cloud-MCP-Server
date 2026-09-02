"""Configuration management for the Qlik Cloud MCP Server."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

VALID_TRANSPORTS = ("stdio", "streamable-http", "sse")


def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} placeholders with environment variable values."""

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            logger.warning("Environment variable %s is not set", var_name)
            return match.group(0)
        return env_value

    return _ENV_VAR_PATTERN.sub(_replace, value)


def _resolve_dict(data: dict) -> dict:
    """Recursively resolve environment variables in a dictionary."""
    resolved: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            resolved[key] = _resolve_env_vars(value)
        elif isinstance(value, dict):
            resolved[key] = _resolve_dict(value)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_env_vars(v) if isinstance(v, str)
                else _resolve_dict(v) if isinstance(v, dict)
                else v
                for v in value
            ]
        else:
            resolved[key] = value
    return resolved


def _tenant_url_errors(url: str) -> list[str]:
    """Reject anything other than a bare https://host[:port] tenant URL.

    Every REST and WebSocket URL is built from this value, so a path,
    query, fragment, or embedded credentials would end up in requests.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return ["qlik.tenant_url must start with https://"]
    if not parsed.hostname:
        return ["qlik.tenant_url must include a hostname"]
    if parsed.username or parsed.password:
        return ["qlik.tenant_url must not contain credentials"]
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return ["qlik.tenant_url must be the bare tenant origin, e.g. https://tenant.us.qlikcloud.com"]
    return []


@dataclass
class OAuthConfig:
    client_id: str = ""
    client_secret: str = ""
    token_url: str = ""


@dataclass
class QlikConfig:
    tenant_url: str = ""
    api_key: str = ""
    oauth: Optional[OAuthConfig] = None
    default_app_id: str = ""
    timeout_seconds: int = 30
    max_retries: int = 3


@dataclass
class ServerConfig:
    transport: str = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = 8080
    http_path: str = "/mcp"
    log_level: str = "INFO"


# Config keys from the SSE-only era, mapped onto the transport-neutral names.
_LEGACY_SERVER_KEYS = {"sse_host": "http_host", "sse_port": "http_port"}


# Boolean switches from the four-tool era, kept for backward compatibility.
_LEGACY_TOOL_FLAGS = {
    "qlik_search": "search",
    "qlik_get_fields": "get_fields",
    "qlik_get_sheet_details": "get_sheet_details",
    "qlik_get_hypercube_data": "get_hypercube_data",
    "qlik_create_sheet": "create_sheet",
}


@dataclass
class ToolSettings:
    get_sheet_details: bool = True
    get_hypercube_data: bool = True
    get_fields: bool = True
    create_sheet: bool = True
    search: bool = True
    disabled_tools: list[str] = field(default_factory=list)
    max_hypercube_rows: int = 10000
    max_hypercube_columns: int = 50
    allow_sheet_creation: bool = True
    created_sheet_prefix: str = "[Agent] "

    def is_enabled(self, tool_name: str) -> bool:
        """Whether a tool should be registered (write gating is applied separately)."""
        if tool_name in {name.strip() for name in self.disabled_tools}:
            return False
        flag = _LEGACY_TOOL_FLAGS.get(tool_name)
        if flag is not None and not getattr(self, flag):
            return False
        return True


@dataclass
class Config:
    qlik: QlikConfig = field(default_factory=QlikConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    tools: ToolSettings = field(default_factory=ToolSettings)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        data = _resolve_dict(raw)
        return cls._from_dict(data)

    @classmethod
    def from_env(cls) -> Config:
        """Build config from environment variables only."""
        config = cls()
        env = os.environ
        config.qlik.tenant_url = env.get("QLIK_TENANT_URL", "")
        config.qlik.api_key = env.get("QLIK_API_KEY", "")
        config.qlik.default_app_id = env.get("QLIK_DEFAULT_APP_ID", "")

        client_id = env.get("QLIK_OAUTH_CLIENT_ID", "")
        if client_id:
            config.qlik.oauth = OAuthConfig(
                client_id=client_id,
                client_secret=env.get("QLIK_OAUTH_CLIENT_SECRET", ""),
                token_url=env.get("QLIK_OAUTH_TOKEN_URL", ""),
            )

        if env.get("QLIK_MCP_DISABLED_TOOLS"):
            config.tools.disabled_tools = [
                name.strip() for name in env["QLIK_MCP_DISABLED_TOOLS"].split(",") if name.strip()
            ]
        if env.get("QLIK_MCP_TRANSPORT"):
            config.server.transport = env["QLIK_MCP_TRANSPORT"]
        if env.get("QLIK_MCP_HTTP_PORT"):
            try:
                config.server.http_port = int(env["QLIK_MCP_HTTP_PORT"])
            except ValueError:
                logger.warning("QLIK_MCP_HTTP_PORT is not an integer, ignoring")
        return config

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Config:
        config = cls()

        if "qlik" in data and data["qlik"]:
            qlik_data = data["qlik"]
            oauth_data = qlik_data.get("oauth", None)
            config.qlik = QlikConfig(**{
                k: v for k, v in qlik_data.items()
                if k in QlikConfig.__dataclass_fields__ and k != "oauth"
            })
            if oauth_data:
                config.qlik.oauth = OAuthConfig(**{
                    k: v for k, v in oauth_data.items()
                    if k in OAuthConfig.__dataclass_fields__
                })

        if "server" in data and data["server"]:
            server_data = {
                _LEGACY_SERVER_KEYS.get(k, k): v for k, v in data["server"].items()
            }
            config.server = ServerConfig(**{
                k: v for k, v in server_data.items()
                if k in ServerConfig.__dataclass_fields__
            })

        if "tools" in data and data["tools"]:
            tools_data = dict(data["tools"])
            disabled = tools_data.get("disabled_tools")
            if isinstance(disabled, str):
                tools_data["disabled_tools"] = [n.strip() for n in disabled.split(",") if n.strip()]
            elif disabled is None:
                tools_data.pop("disabled_tools", None)
            config.tools = ToolSettings(**{
                k: v for k, v in tools_data.items()
                if k in ToolSettings.__dataclass_fields__
            })

        return config

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of errors (empty = valid)."""
        errors: list[str] = []

        if not self.qlik.tenant_url:
            errors.append("qlik.tenant_url is required")
        else:
            errors.extend(_tenant_url_errors(self.qlik.tenant_url))

        has_api_key = bool(self.qlik.api_key) and not self.qlik.api_key.startswith("${")
        has_oauth = (
            self.qlik.oauth is not None
            and bool(self.qlik.oauth.client_id)
            and not self.qlik.oauth.client_id.startswith("${")
        )

        if not has_api_key and not has_oauth:
            errors.append(
                "Authentication required: set qlik.api_key or qlik.oauth credentials"
            )

        if self.server.transport not in VALID_TRANSPORTS:
            errors.append(
                "server.transport must be one of "
                f"{', '.join(VALID_TRANSPORTS)}, got '{self.server.transport}'"
            )

        port = self.server.http_port
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            errors.append("server.http_port must be an integer between 1 and 65535")

        if self.tools.max_hypercube_rows < 1:
            errors.append("tools.max_hypercube_rows must be >= 1")

        return errors

    @property
    def auth_mode(self) -> str:
        """Return the active auth mode: 'api_key' or 'oauth'."""
        if self.qlik.oauth and self.qlik.oauth.client_id:
            return "oauth"
        return "api_key"

    @property
    def tenant_host(self) -> str:
        """Hostname of the tenant URL (no scheme, path, or credentials)."""
        return urlparse(self.qlik.tenant_url).hostname or ""
