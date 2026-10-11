"""Step 16: Verification tests for Model Context Protocol (MCP) Server."""
from __future__ import annotations
import json
import pytest

from leadflow.crm.sqlite_adapter import SQLiteCRM
from leadflow.tools.registry import build_registry
from leadflow.mcp.server import handle_jsonrpc_request, get_mcp_tools, PROTOCOL_VERSION, SERVER_NAME


@pytest.fixture
def mcp_setup(tmp_path):
    db_file = tmp_path / "mcp_test.db"
    crm = SQLiteCRM(db_file)
    crm.create_contact({
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@analytical.org",
        "company": "Babbage Engines",
        "title": "Lead Mathematician",
    })
    crm.create_deal(
        contact_id=1,
        name="Engine Expansion",
        stage="qualified",
        amount=50000,
    )
    registry = build_registry(crm, actor="mcp-test", mode="read")
    yield crm, registry
    crm.close()


def test_mcp_initialize(mcp_setup):
    crm, registry = mcp_setup
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }
    res = handle_jsonrpc_request(req, crm, registry)
    assert res is not None
    assert res["id"] == 1
    assert res["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert res["result"]["serverInfo"]["name"] == SERVER_NAME
    assert "tools" in res["result"]["capabilities"]


def test_mcp_ping(mcp_setup):
    crm, registry = mcp_setup
    req = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    res = handle_jsonrpc_request(req, crm, registry)
    assert res["id"] == 2
    assert res["result"] == {}


def test_mcp_tools_list(mcp_setup):
    crm, registry = mcp_setup
    req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    res = handle_jsonrpc_request(req, crm, registry)
    assert res["id"] == 3
    tools = res["result"]["tools"]
    tool_names = [t["name"] for t in tools]

    assert "search_contact" in tool_names
    assert "get_contact_details" in tool_names
    assert "get_crm_stats" in tool_names
    assert "score_lead_inquiry" in tool_names
    # Write tools must NOT be exposed
    assert "create_contact" not in tool_names
    assert "create_deal" not in tool_names


def test_mcp_tools_call_get_crm_stats(mcp_setup):
    crm, registry = mcp_setup
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "get_crm_stats",
            "arguments": {},
        },
    }
    res = handle_jsonrpc_request(req, crm, registry)
    assert res["id"] == 4
    assert res["result"]["isError"] is False
    content_text = res["result"]["content"][0]["text"]
    data = json.loads(content_text)
    assert data["contacts"] == 1
    assert data["deals"] == 1


def test_mcp_tools_call_search_contact(mcp_setup):
    crm, registry = mcp_setup
    req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "search_contact",
            "arguments": {"email": "ada@analytical.org"},
        },
    }
    res = handle_jsonrpc_request(req, crm, registry)
    assert res["id"] == 5
    assert res["result"]["isError"] is False
    content_text = res["result"]["content"][0]["text"]
    data = json.loads(content_text)
    assert len(data["matches"]) == 1
    assert data["matches"][0]["first_name"] == "Ada"


def test_mcp_unknown_method(mcp_setup):
    crm, registry = mcp_setup
    req = {"jsonrpc": "2.0", "id": 6, "method": "unknown_rpc_method"}
    res = handle_jsonrpc_request(req, crm, registry)
    assert res["id"] == 6
    assert "error" in res
    assert res["error"]["code"] == -32601
