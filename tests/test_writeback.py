import json
import pytest
from leadflow.crm import SQLiteCRM
from leadflow.workflow.approval import ApprovalDecision, ApprovalRequest
from leadflow.workflow.writeback import execute_write_back, WriteBackResult


@pytest.fixture
def crm_instance(tmp_path):
    crm = SQLiteCRM(tmp_path / "test_crm.db")
    return crm


def make_request_and_decision(status="approved", tier="hot", email="sarah@example.com", is_existing=False, match_id=None):
    req = ApprovalRequest(
        id="req-001",
        created_at="2026-10-05T12:00:00Z",
        status=status,
        lead_summary={
            "first_name": "Sarah",
            "last_name": "Connor",
            "email": email,
            "company_name": "Cyberdyne",
            "job_title": "Security Lead",
            "intent": "demo_request",
        },
        dedupe_info={"decision": "existing" if is_existing else "new", "match_ids": [match_id] if match_id else []},
        score_info={"score": 85, "tier": tier},
        draft_email={"subject": "Follow up on demo", "body": "Let's schedule next week."},
        security_flag=False,
    )
    decision = ApprovalDecision(
        request_id="req-001",
        status=status,
        reviewer="rep_alice",
        decided_at="2026-10-05T12:05:00Z",
        final_subject="Follow up on demo",
        final_body="Let's schedule next week.",
        final_tier=tier,
        notes="Looks great",
    )
    return req, decision


def test_happy_path_hot_lead(crm_instance):
    crm = crm_instance
    req, decision = make_request_and_decision(status="approved", tier="hot")

    res = execute_write_back(req, decision, crm)
    assert res.success is True
    assert res.contact_id is not None
    assert res.deal_id is not None
    assert res.interaction_id is not None
    assert res.email_sent is True
    assert res.replayed is False

    # Verify contact in DB
    contact = crm.get_contact(res.contact_id)
    assert contact["first_name"] == "Sarah"
    assert contact["email"] == "sarah@example.com"

    # Verify deal in DB
    deals = crm.list_deals(res.contact_id)
    assert len(deals) == 1
    assert deals[0]["amount"] == 25000.0

    # Verify interaction in DB
    interactions = crm.list_interactions(res.contact_id)
    assert len(interactions) == 1
    assert "Follow up on demo" in interactions[0]["summary"]


def test_cold_lead_does_not_create_deal(crm_instance):
    crm = crm_instance
    req, decision = make_request_and_decision(status="approved", tier="cold")

    res = execute_write_back(req, decision, crm)
    assert res.success is True
    assert res.contact_id is not None
    assert res.deal_id is None  # Cold leads don't create active pipeline deals
    assert res.interaction_id is not None


def test_existing_contact_updates_without_duplicate(crm_instance):
    crm = crm_instance
    # Seed original contact
    original = crm.create_contact({
        "first_name": "Sarah",
        "last_name": "Connor",
        "email": "sarah@example.com",
        "company_name": "Old Company",
        "title": "Analyst",
    }, actor="test")

    req, decision = make_request_and_decision(
        status="approved",
        tier="hot",
        is_existing=True,
        match_id=original["id"]
    )

    res = execute_write_back(req, decision, crm)
    assert res.success is True
    assert res.contact_id == original["id"]

    # Verify title was updated
    updated = crm.get_contact(original["id"])
    assert updated["title"] == "Security Lead"
    assert updated["company_name"] == "Cyberdyne"

    # Verify only 1 contact in total
    assert crm.stats()["contacts"] == 1


def test_idempotent_replay_prevents_duplicate_records(crm_instance):
    crm = crm_instance
    req, decision = make_request_and_decision(status="approved", tier="hot")

    res1 = execute_write_back(req, decision, crm, idempotency_key="key-12345")
    assert res1.success is True
    assert res1.replayed is False

    # Second call with same idempotency key
    res2 = execute_write_back(req, decision, crm, idempotency_key="key-12345")
    assert res2.success is True
    assert res2.replayed is True
    assert res2.contact_id == res1.contact_id
    assert res2.deal_id == res1.deal_id

    # Verify counts in DB
    stats = crm.stats()
    assert stats["contacts"] == 1
    assert stats["deals"] == 1
    assert stats["interactions"] == 1


def test_unapproved_write_back_is_blocked(crm_instance):
    crm = crm_instance
    req, decision = make_request_and_decision(status="rejected")

    with pytest.raises(PermissionError, match="Human approval is required"):
        execute_write_back(req, decision, crm)

    assert crm.stats()["contacts"] == 0


def test_rollback_on_partial_failure(crm_instance):
    crm = crm_instance
    req, decision = make_request_and_decision(status="approved", tier="hot")

    # Inject a failure into the email sending step
    def failing_sender(to, subject, body):
        raise RuntimeError("SMTP server connection timeout")

    res = execute_write_back(req, decision, crm, send_email_fn=failing_sender)
    assert res.success is False
    assert "SMTP server connection timeout" in res.error

    # CRITICAL: Verify transactional rollback!
    # Because email sending failed, the contact, deal, and interaction must NOT exist in DB!
    stats = crm.stats()
    assert stats["contacts"] == 0
    assert stats["deals"] == 0
    assert stats["interactions"] == 0

    # Verify failure was audited
    audit = crm._one("SELECT * FROM audit_log WHERE action = 'write_back_failed'")
    assert audit is not None
    assert "SMTP server connection timeout" in audit["detail"]
