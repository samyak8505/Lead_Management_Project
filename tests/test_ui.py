"""Step 17: UI and Web Application Integration Tests."""
from __future__ import annotations
import json
import threading
import time
import urllib.request
import pytest
from leadflow.crm.sqlite_adapter import SQLiteCRM
from leadflow.ui.app import start_server, LeadFlowHandler
from socketserver import ThreadingTCPServer


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ui_test")
    db_file = tmp / "ui_test.db"
    crm = SQLiteCRM(db_file)
    crm.create_contact({
        "first_name": "Test",
        "last_name": "User",
        "email": "test.user@example.com",
        "company": "Acme Test",
        "title": "Tester",
    })
    crm.close()

    port = 8899
    LeadFlowHandler.crm_path = str(db_file)
    httpd = ThreadingTCPServer(("127.0.0.1", port), LeadFlowHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.2)  # Wait for socket to bind

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, str(db_file)

    httpd.shutdown()
    httpd.server_close()


def test_ui_serves_html_index(ui_server):
    base_url, _ = ui_server
    req = urllib.request.Request(f"{base_url}/")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "<title>LeadFlow" in content
        assert "Autonomous CRM Pipeline" in content


def test_ui_api_stats(ui_server):
    base_url, _ = ui_server
    req = urllib.request.Request(f"{base_url}/api/stats")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "crm" in data
        assert "approvals" in data
        assert "telemetry" in data
        assert data["crm"]["contacts"] >= 1


def test_ui_api_process_lead(ui_server):
    base_url, _ = ui_server
    payload = json.dumps({
        "raw_lead": "Hi, I am James Wilson, CTO at Wilson Tech (james@wilsontech.com). We have a budget of $30,000 for LeadFlow and want to start in 2 weeks.",
        "channel": "email",
        "auto_approve": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/api/process",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        state = json.loads(resp.read().decode("utf-8"))
        assert state["status"] in ("waiting_for_approval", "completed")
        assert state["extraction"]["first_name"] == "James"
        assert state["scoring"]["tier"] == "hot"
        assert state["approval_request_id"] is not None


def test_ui_api_approvals_and_decision(ui_server):
    base_url, _ = ui_server
    # 1. Fetch approvals list
    req = urllib.request.Request(f"{base_url}/api/approvals")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        approvals = json.loads(resp.read().decode("utf-8"))
        assert len(approvals) >= 1
        pending = [a for a in approvals if a["status"] == "pending"]
        assert len(pending) >= 1
        target_id = pending[0]["id"]

    # 2. Approve via API
    decide_payload = json.dumps({
        "request_id": target_id,
        "action": "approve",
        "reviewer": "test_sales_rep",
    }).encode("utf-8")

    decide_req = urllib.request.Request(
        f"{base_url}/api/approvals/decide",
        data=decide_payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(decide_req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["decision"]["status"] == "approved"
        assert data["write_back"]["success"] is True
        assert data["write_back"]["contact_id"] is not None
