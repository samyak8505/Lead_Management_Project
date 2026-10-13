"""Phase 4: SaaS Orchestrator & Webhook Ingestion API Unit Tests."""
from __future__ import annotations
import base64
import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from leadflow.crm import SQLiteCRM, SalesforceCRM, HubSpotCRM, get_crm_for_tenant
from leadflow.tenancy.manager import TenantManager
from leadflow.ui.app import LeadFlowHandler
from leadflow.workflow.approval import ApprovalGate
from leadflow.workflow.orchestrator import run_lead_pipeline


@pytest.fixture
def test_setup(tmp_path: Path):
    db_file = tmp_path / "crm_test.db"
    db_str = str(db_file)

    # Bootstrap default schema & test tenant
    crm_init = SQLiteCRM(db_str)
    crm_init.close()
    mgr = TenantManager(db_str)
    tenant = mgr.create_tenant(
        name="Acme Cybersec",
        website="https://acmecybersec.io",
        industry="Cybersecurity",
        api_key="sk_leadflow_test_cybersec_123",
        initial_settings={
            "competitors": ["CrowdStrike", "SentinelOne"],
            "brand_pitch": "Next-generation autonomous endpoint protection.",
            "auto_approve_hot": True,
        },
        initial_crm={
            "provider": "salesforce",
            "instance_url": "https://test.salesforce.com",
            "is_sandbox": True,
        },
    )

    # Start test HTTP server in background thread
    LeadFlowHandler.crm_path = db_str
    import socketserver
    httpd = socketserver.TCPServer(("127.0.0.1", 0), LeadFlowHandler)
    port = httpd.server_address[1]

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    yield {
        "db_path": db_str,
        "tenant": tenant,
        "manager": mgr,
        "port": port,
    }

    httpd.shutdown()
    httpd.server_close()


def test_saas_orchestrator_multi_tenant_execution(test_setup):
    db_path = test_setup["db_path"]
    tenant = test_setup["tenant"]

    lead_text = "Hi, we are currently evaluating CrowdStrike for 200 endpoints. Can you share enterprise pricing?"
    state = run_lead_pipeline(
        raw=lead_text,
        channel="web_form",
        tenant_id=tenant.id,
        auto_approve=True,
        db_path=db_path,
    )

    assert state.tenant_id == tenant.id
    assert state.status in ("completed", "waiting_for_approval")
    assert state.dossier is not None
    assert state.battlecard is not None
    # Tenant's custom competitor should be detected
    assert state.battlecard.get("competitor_detected") == "CrowdStrike"
    assert state.battlecard.get("battlecard_type") == "competitor_replacement"


def test_saas_orchestrator_document_rfp_ingestion(test_setup):
    db_path = test_setup["db_path"]
    tenant = test_setup["tenant"]

    rfp_text = """
    RFP Specifications:
    We need 120 seats for our security operations center.
    Mandatory compliance: SOC-2, HIPAA.
    Target budget is $75k.
    """
    raw_lead = "Please review attached requirements."

    state = run_lead_pipeline(
        raw=raw_lead,
        channel="email",
        tenant_id=tenant.id,
        document_bytes=rfp_text.encode("utf-8"),
        document_filename="soc_rfp.txt",
        auto_approve=True,
        db_path=db_path,
    )

    assert state.document_analysis is not None
    reqs = state.document_analysis.get("requirements", {})
    assert reqs.get("seat_count") == 120
    assert reqs.get("budget_amount") == 75000.0
    assert "SOC-2" in reqs.get("compliance_needs", [])
    assert "HIPAA" in reqs.get("compliance_needs", [])


def test_webhook_ingest_unauthorized_when_missing_or_invalid_key(test_setup):
    port = test_setup["port"]
    conn = http.client.HTTPConnection("127.0.0.1", port)

    # 1. Missing key
    payload = json.dumps({"lead_text": "Hi, I need pricing."})
    conn.request("POST", "/api/v1/ingest", body=payload, headers={"Content-Type": "application/json"})
    res1 = conn.getresponse()
    assert res1.status == 401
    body1 = json.loads(res1.read().decode())
    assert "Unauthorized" in body1.get("error", "")

    # 2. Invalid key
    conn.request(
        "POST",
        "/api/v1/ingest",
        body=payload,
        headers={"Content-Type": "application/json", "X-API-Key": "sk_invalid_random_key"},
    )
    res2 = conn.getresponse()
    assert res2.status == 401
    conn.close()


def test_webhook_ingest_successful_json_and_document(test_setup):
    port = test_setup["port"]
    tenant = test_setup["tenant"]
    conn = http.client.HTTPConnection("127.0.0.1", port)

    doc_text = "Enterprise RFP: Requires 80 licenses and SOC-2 compliance with $50k annual budget."
    payload = {
        "lead_text": "We need immediate assistance replacing SentinelOne. See spec attached.",
        "channel": "webhook",
        "document_text": doc_text,
        "document_filename": "spec.txt",
        "auto_approve": True,
    }

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": tenant.api_key,
    }

    conn.request("POST", "/api/v1/ingest", body=json.dumps(payload), headers=headers)
    res = conn.getresponse()
    assert res.status == 200

    data = json.loads(res.read().decode())
    assert data["success"] is True
    assert data["tenant"]["id"] == tenant.id
    assert data["battlecard"]["competitor_detected"] == "SentinelOne"
    assert data["document_analysis"]["requirements"]["seat_count"] == 80
    conn.close()


def test_tenant_api_endpoints(test_setup):
    port = test_setup["port"]
    tenant = test_setup["tenant"]
    conn = http.client.HTTPConnection("127.0.0.1", port)

    # 1. GET /api/tenants
    conn.request("GET", "/api/tenants")
    res1 = conn.getresponse()
    assert res1.status == 200
    tenants = json.loads(res1.read().decode())
    assert any(t["id"] == tenant.id for t in tenants)

    # 2. POST /api/tenants/settings
    update_payload = json.dumps({
        "tenant_id": tenant.id,
        "tone": "executive",
        "min_budget": 50000.0,
    })
    conn.request("POST", "/api/tenants/settings", body=update_payload, headers={"Content-Type": "application/json"})
    res2 = conn.getresponse()
    assert res2.status == 200
    updated_settings = json.loads(res2.read().decode())
    assert updated_settings["tone"] == "executive"
    assert updated_settings["min_budget"] == 50000.0
    conn.close()
