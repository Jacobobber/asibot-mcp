"""Asibot MCP server — stdio transport entry point for Claude Desktop."""

from asibot.config import settings
from asibot.server import mcp, _setup_connectors


def main() -> None:
    settings.ensure_dirs()
    _setup_connectors()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
