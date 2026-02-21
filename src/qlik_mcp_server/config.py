"""Configuration management for the Qlik Cloud MCP Server."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


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
    resolved = {}
    for key, value in data.items():
        if isinstance(value, str):
            resolved[key] = _resolve_env_vars(value)
        elif isinstance(value, dict):
            resolved[key] = _resolve_dict(value)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_env_vars(v) if isinstance(v, str) else v for v in value
            ]
        else:
            resolved[key] = value
    return resolved


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
    sse_host: str = "0.0.0.0"
    sse_port: int = 8080
    log_level: str = "INFO"


@dataclass
class ToolSettings:
    get_sheet_details: bool = True
    get_hypercube_data: bool = True
    create_sheet: bool = True
    search: bool = True
    max_hypercube_rows: int = 10000
    max_hypercube_columns: int = 50
    allow_sheet_creation: bool = True
    created_sheet_prefix: str = "[Agent] "


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

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        data = _resolve_dict(raw)
        return cls._from_dict(data)

    @classmethod
    def from_env(cls) -> Config:
        """Build config from environment variables only."""
        config = cls()
        config.qlik.tenant_url = os.environ.get("QLIK_TENANT_URL", "")
        config.qlik.api_key = os.environ.get("QLIK_API_KEY", "")
        return config

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Config:
        config = cls()

        if "qlik" in data:
            qlik_data = data["qlik"]
            oauth_data = qlik_data.pop("oauth", None)
            config.qlik = QlikConfig(**{
                k: v for k, v in qlik_data.items()
                if k in QlikConfig.__dataclass_fields__
            })
            if oauth_data:
                config.qlik.oauth = OAuthConfig(**{
                    k: v for k, v in oauth_data.items()
                    if k in OAuthConfig.__dataclass_fields__
                })

        if "server" in data:
            config.server = ServerConfig(**{
                k: v for k, v in data["server"].items()
                if k in ServerConfig.__dataclass_fields__
            })

        if "tools" in data:
            config.tools = ToolSettings(**{
                k: v for k, v in data["tools"].items()
                if k in ToolSettings.__dataclass_fields__
            })

        return config

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of errors (empty = valid)."""
        errors: list[str] = []

        if not self.qlik.tenant_url:
            errors.append("qlik.tenant_url is required")
        elif not self.qlik.tenant_url.startswith("https://"):
            errors.append("qlik.tenant_url must start with https://")

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

        if self.server.transport not in ("stdio", "sse"):
            errors.append(f"server.transport must be 'stdio' or 'sse', got '{self.server.transport}'")

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
        """Extract hostname from tenant URL."""
        return self.qlik.tenant_url.rstrip("/").replace("https://", "")
