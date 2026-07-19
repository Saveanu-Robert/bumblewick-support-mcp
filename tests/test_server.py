"""Smoke tests for the MCP wiring itself (tools/resources/prompts registered
and callable). Points the server at a temporary seeded DB via the environment
before importing it."""

import os
import tempfile

from bumblewick_support.seed import seed

# Configure + seed a temp DB BEFORE importing the server module (it binds settings
# and the SupportDesk at import time).
_TMP_DB = os.path.join(tempfile.mkdtemp(), "server-test.db")
os.environ["BUMBLEWICK_DB_PATH"] = _TMP_DB
seed(_TMP_DB)

from bumblewick_support import server as srv  # noqa: E402


async def test_tools_registered():
    names = {t.name for t in await srv.mcp.list_tools()}
    assert {"find_orders", "get_order", "search_help", "create_refund"} <= names


async def test_prompts_registered():
    names = {p.name for p in await srv.mcp.list_prompts()}
    assert "draft_apology" in names


async def test_call_read_tool_returns_data():
    result = await srv.mcp.call_tool("find_orders", {"query": "cus_ivy"})
    assert "ord_1001" in str(result)


async def test_read_policy_resource():
    result = await srv.mcp.read_resource("bumblewick://policy/refunds")
    assert "Refund Policy" in str(result)


async def test_read_customer_resource_template():
    result = await srv.mcp.read_resource("bumblewick://customer/cus_bram")
    assert "Bram Kettle" in str(result)


async def test_get_order_unknown_returns_clean_error():
    result = await srv.mcp.call_tool("get_order", {"order_id": "ord_nope"})
    assert "not found" in str(result).lower()


async def test_customer_resource_unknown_is_handled():
    result = await srv.mcp.read_resource("bumblewick://customer/cus_nope")
    assert "No customer found" in str(result)


async def test_refund_needs_confirmation_via_protocol():
    result = await srv.mcp.call_tool(
        "create_refund",
        {"order_id": "ord_1004", "amount_cents": 9900, "reason": "returned"},
    )
    assert "needs_confirmation" in str(result)
