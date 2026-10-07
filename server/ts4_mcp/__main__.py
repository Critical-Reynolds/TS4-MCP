"""Entry point: `python -m ts4_mcp` runs the MCP server over stdio."""
from __future__ import annotations

import sys


def main() -> None:
    from ts4_mcp.server import mcp

    sys.stderr.write("[ts4_mcp] starting stdio server\n")
    sys.stderr.flush()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
