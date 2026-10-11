import json
import sqlite3
import pytest
from leadflow.crm import SQLiteCRM
from leadflow.workflow.approval import ApprovalGate, ApprovalRequest, render_html_dashboard


@pytest.fixture
def gate_instance(tmp_path):
    # Initialize CRM so audit_log exists
    crm = SQLiteCRM(tmp_path / "test_crm.db")
    gate = ApprovalGate(tmp_path / "test_crm.db")
    return gate, tmp_path / "test_crm.db"


def test_create_and_get_request(gate_instance):
    gate, _ = gate_instance
    lead = {"first_name": "Jordan", "company_name": "Acme", "intent": "demo_request"}
    score = {"score": 85, "tier": "hot"}
    draft = {"subject": "Demo Request", "body": "Hi Jordan, let's schedule."}

    req = gate.create_request(lead, {"decision": "new"}, score, draft, security_flag=False)
    assert req.status == "pending"
    assert req.id is not None

    fetched = gate.get_request(req.id)
    assert fetched is not None
    assert fetched.lead_summary["first_name"] == "Jordan"
    assert fetched.score_info["score"] == 85
    assert fetched.draft_email["subject"] == "Demo Request"


def test_approve_lead_logs_audit(gate_instance):
    gate, db_path = gate_instance
    lead = {"first_name": "Sarah", "company_name": "Peak Inc"}
    req = gate.create_request(lead, {}, {"tier": "hot"}, {"subject": "Intro", "body": "Hello"})

    decision = gate.approve(req.id, reviewer="alice_rep", notes="Verified company size")
    assert decision.status == "approved"
    assert decision.reviewer == "alice_rep"
    assert decision.final_subject == "Intro"

    # Verify updated in DB
    updated = gate.get_request(req.id)
    assert updated.status == "approved"
    assert updated.reviewer == "alice_rep"

    # Verify audit trail
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        log = conn.execute("SELECT * FROM audit_log WHERE action = 'approve_lead'").fetchone()
        assert log is not None
        assert log["actor"] == "alice_rep"
        assert "Verified company size" in log["detail"]


def test_edit_lead_updates_draft_and_tier(gate_instance):
    gate, db_path = gate_instance
    lead = {"first_name": "David", "company_name": "Global Corp"}
    req = gate.create_request(lead, {}, {"tier": "warm"}, {"subject": "Original Subj", "body": "Original Body"})

    decision = gate.edit(
        req.id,
        reviewer="bob_rep",
        edited_subject="Customized Subject",
        edited_body="Customized Body",
        edited_tier="hot",
        notes="Customer has 500 seats",
    )
    assert decision.status == "edited"
    assert decision.final_subject == "Customized Subject"
    assert decision.final_body == "Customized Body"
    assert decision.final_tier == "hot"

    updated = gate.get_request(req.id)
    assert updated.status == "edited"
    assert updated.edited_tier == "hot"

    # Verify audit log recorded changes
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        log = conn.execute("SELECT * FROM audit_log WHERE action = 'edit_and_approve_lead'").fetchone()
        assert log is not None
        assert log["actor"] == "bob_rep"
        detail = json.loads(log["detail"])
        assert detail["original_tier"] == "warm"
        assert detail["final_tier"] == "hot"


def test_reject_lead_stops_pipeline(gate_instance):
    gate, db_path = gate_instance
    lead = {"first_name": "Spammer", "company_name": "SEO Spam"}
    req = gate.create_request(lead, {}, {"tier": "none"}, {"subject": "Promo", "body": "Spam"})

    decision = gate.reject(req.id, reviewer="charlie_rep", reason="Spam backlink offer")
    assert decision.status == "rejected"
    assert decision.reviewer == "charlie_rep"
    assert decision.notes == "Spam backlink offer"

    updated = gate.get_request(req.id)
    assert updated.status == "rejected"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        log = conn.execute("SELECT * FROM audit_log WHERE action = 'reject_lead'").fetchone()
        assert log is not None
        assert log["actor"] == "charlie_rep"


def test_cannot_decide_twice(gate_instance):
    gate, _ = gate_instance
    req = gate.create_request({"first_name": "Test"}, {}, {"tier": "hot"}, {"subject": "X", "body": "Y"})
    gate.approve(req.id, reviewer="rep")
    with pytest.raises(ValueError):
        gate.approve(req.id, reviewer="rep")


def test_render_html_dashboard():
    req = ApprovalRequest(
        id="test-123",
        created_at="2026-10-05T12:00:00Z",
        status="pending",
        lead_summary={"first_name": "Alice", "company_name": "Wonderland", "intent": "demo_request"},
        dedupe_info={"decision": "new"},
        score_info={"score": 90, "tier": "hot"},
        draft_email={"subject": "Welcome Alice", "body": "Let's connect tomorrow."},
        security_flag=True,
    )
    html = render_html_dashboard([req])
    assert "Alice" in html
    assert "Wonderland" in html
    assert "Welcome Alice" in html
    assert "HOT (90)" in html
    assert "Prompt injection detected" in html
    assert "Approve & Send" in html
    assert "Reject" in html
