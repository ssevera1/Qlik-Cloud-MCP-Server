"""CLI entry point for the Qlik Cloud MCP Server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Config


def setup_logging(level: str = "INFO") -> None:
    """Configure logging format and level."""
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
        description="Qlik Cloud MCP Server — Expose Qlik Cloud capabilities as MCP tools for AI agents.",
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
        choices=["stdio", "sse"],
        default=None,
        help="Transport mode (overrides config file)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="SSE port (overrides config file, only used with --transport=sse)",
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

    # Load configuration
    config_path = Path(args.config)
    if config_path.exists():
        config = Config.load(config_path)
    else:
        # Try environment variables only
        config = Config.from_env()
        if not config.qlik.tenant_url:
            print(
                f"Error: Config file '{args.config}' not found and "
                "QLIK_TENANT_URL environment variable is not set.\n"
                "Run 'qlik-mcp-server --help' for usage.",
                file=sys.stderr,
            )
            return 1

    # CLI overrides
    if args.transport:
        config.server.transport = args.transport
    if args.port:
        config.server.sse_port = args.port
    if args.verbose:
        config.server.log_level = "DEBUG"

    setup_logging(config.server.log_level)

    # Validate
    errors = config.validate()
    if errors:
        for err in errors:
            logging.error("Config error: %s", err)
        return 1

    if args.validate:
        print("Configuration is valid.")
        print(f"  Tenant: {config.tenant_host}")
        print(f"  Auth: {config.auth_mode}")
        print(f"  Transport: {config.server.transport}")
        tools_enabled = sum([
            config.tools.get_sheet_details,
            config.tools.get_hypercube_data,
            config.tools.create_sheet,
            config.tools.search,
        ])
        print(f"  Tools enabled: {tools_enabled}/4")
        return 0

    # Run server
    from .server import run_sse_server, run_stdio_server

    if config.server.transport == "sse":
        asyncio.run(run_sse_server(config))
    else:
        asyncio.run(run_stdio_server(config))

    return 0


if __name__ == "__main__":
    sys.exit(main())
