"""Abstract base class for connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from mcp.server.fastmcp import FastMCP


@dataclass
class Document:
    """A document fetched by a connector."""

    content: str
    source: str
    source_name: str
    metadata: dict = field(default_factory=dict)


class Connector(ABC):
    """Base class for all connectors.

    Lifecycle: __init__ -> connect() -> register_tools() -> fetch_documents() -> disconnect()
    """

    def __init__(self, name: str, config: dict | None = None) -> None:
        self.name = name
        self.config = config or {}

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection. Raise on failure."""

    async def disconnect(self) -> None:
        """Clean up resources. Default is a no-op; override if cleanup is needed."""

    @abstractmethod
    async def fetch_documents(self) -> list[Document]:
        """Fetch documents from the connected service."""

    def register_tools(self, mcp: FastMCP) -> None:
        """Register connector-specific MCP tools. Override to add tools."""
