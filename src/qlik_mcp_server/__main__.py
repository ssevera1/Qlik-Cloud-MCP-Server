"""Allow running as `python -m qlik_mcp_server`."""

import sys

from .cli import main

sys.exit(main())
