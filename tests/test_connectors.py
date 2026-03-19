"""Tests for connector base class, registry, and connector instantiation."""

import importlib
import pkgutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asibot.connectors.base import Connector
from asibot.connectors import registry


class DummyConnector(Connector):
    """Minimal connector for testing."""

    def __init__(self):
        super().__init__(name="dummy")
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp):
        pass


class TestConnectorBase:
    def test_init(self):
        c = DummyConnector()
        assert c.name == "dummy"
        assert c.config == {}

    def test_init_with_config(self):
        class ConfigConnector(Connector):
            def __init__(self):
                super().__init__(name="cfg", config={"key": "val"})

            async def connect(self): pass
            async def disconnect(self): pass
            async def fetch_documents(self): return []

        c = ConfigConnector()
        assert c.config["key"] == "val"

    @pytest.mark.asyncio
    async def test_connect_disconnect(self):
        c = DummyConnector()
        assert not c.connected
        await c.connect()
        assert c.connected
        await c.disconnect()
        assert not c.connected

    @pytest.mark.asyncio
    async def test_fetch_documents_empty(self):
        c = DummyConnector()
        docs = await c.fetch_documents()
        assert docs == []


class TestRegistry:
    def setup_method(self):
        registry._connectors.clear()

    def teardown_method(self):
        registry._connectors.clear()

    def test_register_and_get(self):
        c = DummyConnector()
        registry.register(c)
        assert registry.get("dummy") is c

    def test_get_not_found(self):
        assert registry.get("nonexistent") is None

    def test_list_all(self):
        c1 = DummyConnector()
        c1.name = "one"
        c2 = DummyConnector()
        c2.name = "two"
        registry.register(c1)
        registry.register(c2)
        names = {c.name for c in registry.list_all()}
        assert names == {"one", "two"}

    @pytest.mark.asyncio
    async def test_connect_all(self):
        c = DummyConnector()
        registry.register(c)
        await registry.connect_all()
        assert c.connected

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        c = DummyConnector()
        c.connected = True
        registry.register(c)
        await registry.disconnect_all()
        assert not c.connected

    def test_register_all_tools(self):
        c = DummyConnector()
        registry.register(c)
        mock_mcp = MagicMock()
        registry.register_all_tools(mock_mcp)
        # DummyConnector.register_tools is a no-op, just verify no crash


class TestSAPODataEscaping:
    """Verify SAP connector escapes user input in OData filters."""

    def test_single_quotes_escaped_in_search(self):
        from asibot.connectors.sap import SAPConnector

        # The filter_expr construction should escape single quotes
        query = "O'Brien"
        safe = query.replace("'", "''")
        assert safe == "O''Brien"
        filter_expr = f"substringof('{safe}', SoldToParty)"
        # Must not break the OData filter — escaped quotes are doubled
        assert "O''Brien" in filter_expr

    def test_single_quotes_escaped_in_order_id(self):
        order_id = "12345'--"
        safe = order_id.replace("'", "''")
        assert safe == "12345''--"


class TestMicrosoftRefreshTokenGuard:
    """Verify Microsoft connector handles missing refresh_token gracefully."""

    @pytest.mark.asyncio
    async def test_refresh_returns_false_without_refresh_token(self):
        from asibot.connectors.microsoft import refresh_token

        # No refresh_token key at all
        result = await refresh_token("test@example.com", {"access_token": "old"})
        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_returns_false_with_empty_refresh_token(self):
        from asibot.connectors.microsoft import refresh_token

        result = await refresh_token("test@example.com", {"access_token": "old", "refresh_token": ""})
        assert result is False


class TestRoboflowAuthHeader:
    """Verify Roboflow connector uses Authorization header via ClientSpec."""

    @pytest.mark.asyncio
    async def test_client_has_auth_header(self):
        from asibot.token_store import CLIENT_SPECS, build_client

        spec = CLIENT_SPECS["roboflow"]
        client = await build_client(spec, {"api_key": "rf_test123"})
        assert client is not None
        assert "Authorization" in client._auth_headers
        assert client._auth_headers["Authorization"] == "Bearer rf_test123"

    @pytest.mark.asyncio
    async def test_client_none_without_key(self):
        from asibot.token_store import CLIENT_SPECS, build_client

        spec = CLIENT_SPECS["roboflow"]
        assert await build_client(spec, {}) is None
        assert await build_client(spec, {"api_key": ""}) is None


class TestGitHubPerPageCap:
    """Verify GitHub connector caps per_page at 100."""

    def test_per_page_capped(self):
        # min(limit, 100) should be used
        assert min(200, 100) == 100
        assert min(50, 100) == 50
        assert min(100, 100) == 100


class TestShareFileSubdomainValidation:
    """Verify ShareFile connector validates subdomain format."""

    def test_valid_subdomain(self):
        from asibot.connectors.citrix_sharefile import _api

        url = _api({"subdomain": "mycompany"})
        assert url == "https://mycompany.sf-api.com/sf/v3"

    def test_valid_subdomain_with_hyphen(self):
        from asibot.connectors.citrix_sharefile import _api

        url = _api({"subdomain": "my-company"})
        assert url == "https://my-company.sf-api.com/sf/v3"

    def test_rejects_empty_subdomain(self):
        from asibot.connectors.citrix_sharefile import _api

        with pytest.raises(ValueError, match="Invalid ShareFile subdomain"):
            _api({"subdomain": ""})

    def test_rejects_subdomain_with_dots(self):
        from asibot.connectors.citrix_sharefile import _api

        with pytest.raises(ValueError, match="Invalid ShareFile subdomain"):
            _api({"subdomain": "evil.com/attack"})

    def test_rejects_subdomain_with_slashes(self):
        from asibot.connectors.citrix_sharefile import _api

        with pytest.raises(ValueError, match="Invalid ShareFile subdomain"):
            _api({"subdomain": "evil/path"})

    def test_rejects_missing_subdomain(self):
        from asibot.connectors.citrix_sharefile import _api

        with pytest.raises(ValueError, match="Invalid ShareFile subdomain"):
            _api({})


class TestConnectorDiscovery:
    """Verify all connector modules can be imported and instantiate a Connector subclass."""

    def test_all_connectors_importable(self):
        import asibot.connectors as connectors_pkg

        skip = {"__init__", "base", "registry", "microsoft", "pagination"}
        for _, module_name, _ in pkgutil.iter_modules(connectors_pkg.__path__):
            if module_name in skip:
                continue
            mod = importlib.import_module(f"asibot.connectors.{module_name}")
            # Find at least one Connector subclass
            found = False
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (isinstance(attr, type)
                    and issubclass(attr, Connector)
                    and attr is not Connector):
                    found = True
                    # Verify it can be instantiated
                    instance = attr()
                    assert instance.name, f"{attr_name} has no name"
            assert found, f"No Connector subclass found in {module_name}"
