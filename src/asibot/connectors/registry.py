"""Connector registry: discover, instantiate, and manage connectors."""

import logging

from mcp.server.fastmcp import FastMCP

from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)

_connectors: dict[str, Connector] = {}


def register(connector: Connector) -> None:
    _connectors[connector.name] = connector
    logger.info("Registered connector: %s", connector.name)


def get(name: str) -> Connector | None:
    return _connectors.get(name)


def list_all() -> list[Connector]:
    return list(_connectors.values())


async def connect_all() -> None:
    for name, connector in _connectors.items():
        try:
            await connector.connect()
            logger.info("Connected: %s", name)
        except (OSError, ValueError, RuntimeError):
            logger.exception("Failed to connect: %s", name)


async def disconnect_all() -> None:
    for name, connector in _connectors.items():
        try:
            await connector.disconnect()
            logger.info("Disconnected: %s", name)
        except (OSError, ValueError, RuntimeError):
            logger.exception("Failed to disconnect: %s", name)


def register_all_tools(mcp: FastMCP) -> None:
    for name, connector in _connectors.items():
        connector.register_tools(mcp)
        logger.info("Registered tools for connector: %s", name)
