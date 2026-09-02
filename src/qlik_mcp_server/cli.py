"""CLI entry point for the Qlik Cloud MCP Server."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import VALID_TRANSPORTS, Config


def setup_logging(level: str = "INFO") -> None:
    """Configure logging to stderr (stdout is reserved for the stdio transport)."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(
        level=numeric_level, format=fmt, datefmt=datefmt, stream=sys.stderr,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="qlik-mcp-server",
        description="Qlik Cloud MCP Server: expose Qlik Cloud capabilities as MCP tools for AI agents.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--transport",
        choices=list(VALID_TRANSPORTS),
        default=None,
        help="Transport mode (overrides config file)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP bind host (overrides config file, used with streamable-http or sse)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP port (overrides config file, used with streamable-http or sse)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate config and exit without starting the server",
    )

    return parser


def main() -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    config_path = Path(args.config)
    if config_path.exists():
        config = Config.load(config_path)
    else:
        config = Config.from_env()
        if not config.qlik.tenant_url:
            print(
                f"Error: Config file '{args.config}' not found and "
                "QLIK_TENANT_URL environment variable is not set.\n"
                "Run 'qlik-mcp-server --help' for usage.",
                file=sys.stderr,
            )
            return 1

    if args.transport:
        config.server.transport = args.transport
    if args.host:
        config.server.http_host = args.host
    if args.port:
        config.server.http_port = args.port
    if args.verbose:
        config.server.log_level = "DEBUG"

    setup_logging(config.server.log_level)

    errors = config.validate()
    if errors:
        for err in errors:
            logging.error("Config error: %s", err)
        return 1

    if args.validate:
        enabled = [
            name for name, flag in (
                ("qlik_search", config.tools.search),
                ("qlik_get_fields", config.tools.get_fields),
                ("qlik_get_sheet_details", config.tools.get_sheet_details),
                ("qlik_get_hypercube_data", config.tools.get_hypercube_data),
                ("qlik_create_sheet", config.tools.create_sheet),
            ) if flag
        ]
        print("Configuration is valid.")
        print(f"  Tenant: {config.tenant_host}")
        print(f"  Auth: {config.auth_mode}")
        print(f"  Transport: {config.server.transport}")
        if config.server.transport != "stdio":
            print(f"  Bind: {config.server.http_host}:{config.server.http_port}")
        print(f"  Tools enabled ({len(enabled)}): {', '.join(enabled)}")
        return 0

    from .server import run_server

    run_server(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
