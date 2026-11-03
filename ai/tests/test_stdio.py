"""Initialize a real stdio child using the official MCP client SDK."""

import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_stdio_handshake_catalog_and_validation():
    script = Path(__file__).resolve().parents[1] / "mcp_server.py"
    params = StdioServerParameters(command=sys.executable, args=[str(script)])
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        assert initialized.serverInfo.name == "pro-ai-bridge"
        catalog = await session.list_tools()
        assert len(catalog.tools) >= 18
        invalid = await session.call_tool(
            "remove_layer", {"layer": "a", "confirm": False}
        )
        assert invalid.isError
        unknown = await session.call_tool("execute_python", {"code": "pass"})
        assert unknown.isError
