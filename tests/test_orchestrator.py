import pytest
from leadflow.crm import SQLiteCRM
from leadflow.workflow.approval import ApprovalGate
from leadflow.workflow.orchestrator import (
    WorkflowState,
    run_lead_pipeline,
    resume_pipeline,
    load_checkpoint,
    save_checkpoint,
    StepLimitExceededError,
    CostLimitExceededError,
)

SAMPLE_HOT_LEAD = """From: "Marcus Vance" <marcus@apexenterprises-demo.com>
Subject: Enterprise CRM Demo

Hello, we want to book a demo for our 200 sales reps. We have budget approved and want to start within 3 weeks.

Marcus Vance
CTO, Apex Enterprises"""

SAMPLE_SPAM = """From: "Backlinks Pro" <seo@spam-network.biz>
Subject: Rank #1 on Google

Guaranteed backlinks 1000 for $50."""

SAMPLE_SUPPORT = """From: "Existing User" <user@customer.com>
Subject: Cannot log in

I am getting a 500 error when logging into my account. Please help reset my password."""


@pytest.fixture
def env_setup(tmp_path):
    db_path = tmp_path / "test_crm.db"
    crm = SQLiteCRM(db_path)
    gate = ApprovalGate(db_path)
    return crm, gate, str(db_path)


def test_end_to_end_sales_lead_auto_approve(env_setup):
    crm, gate, db_path = env_setup

    state = run_lead_pipeline(
        raw=SAMPLE_HOT_LEAD,
        channel="email",
        crm=crm,
        gate=gate,
        auto_approve=True,
        db_path=db_path,
    )

    assert state.status == "completed"
    assert state.extraction["first_name"] == "Marcus"
    assert state.scoring["tier"] == "hot"
    assert state.route["route"] == "sales"
    assert state.draft is not None
    assert state.approval_decision["status"] == "approved"
    assert state.write_back["success"] is True
    assert state.write_back["contact_id"] is not None
    assert state.write_back["deal_id"] is not None
    assert state.write_back["interaction_id"] is not None

    # Verify state was checkpointed in database
    saved = load_checkpoint(state.run_id, db_path)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.step_count >= 5


def test_discard_path_stops_early(env_setup):
    crm, gate, db_path = env_setup

    state = run_lead_pipeline(
        raw=SAMPLE_SPAM,
        channel="email",
        crm=crm,
        gate=gate,
        db_path=db_path,
    )

    assert state.status == "discarded"
    assert state.route["route"] == "discard"
    assert state.draft is None
    assert state.write_back is None
    assert crm.stats()["contacts"] == 0


def test_support_path_stops_early(env_setup):
    crm, gate, db_path = env_setup

    state = run_lead_pipeline(
        raw=SAMPLE_SUPPORT,
        channel="email",
        crm=crm,
        gate=gate,
        db_path=db_path,
    )

    assert state.status == "routed_to_support"
    assert state.route["route"] == "support"
    assert state.draft is None
    assert state.write_back is None
    assert crm.stats()["contacts"] == 0


def test_pause_and_resume_approval_gate(env_setup):
    crm, gate, db_path = env_setup

    # 1. Run pipeline without auto-approve -> pauses at approval_gate
    state = run_lead_pipeline(
        raw=SAMPLE_HOT_LEAD,
        channel="email",
        crm=crm,
        gate=gate,
        auto_approve=False,
        interactive=False,
        db_path=db_path,
    )

    assert state.status == "waiting_for_approval"
    assert state.approval_request_id is not None
    assert state.write_back is None

    # Verify pending request in gate
    req = gate.get_request(state.approval_request_id)
    assert req.status == "pending"

    # 2. Sales rep approves asynchronously
    gate.approve(req.id, reviewer="samyak_sales", notes="Approved pricing and call offer")

    # 3. Resume workflow from checkpoint
    resumed_state = resume_pipeline(state.run_id, db_path=db_path, crm=crm, gate=gate)
    assert resumed_state.status == "completed"
    assert resumed_state.write_back["success"] is True
    assert resumed_state.write_back["contact_id"] is not None


def test_cost_limit_guard():
    state = WorkflowState(run_id="test-run", channel="email", raw_lead="lead text")
    with pytest.raises(CostLimitExceededError):
        # Simulate high token consumption
        state.record_usage({"llm_calls": 5, "input_tokens": 2_000_000, "output_tokens": 500_000})


def test_step_limit_guard(env_setup):
    crm, gate, db_path = env_setup
    state = WorkflowState(run_id="test-step-limit", channel="email", raw_lead="lead", step_count=15)
    save_checkpoint(state, db_path)

    with pytest.raises(StepLimitExceededError):
        run_lead_pipeline(raw="lead", crm=crm, gate=gate, db_path=db_path, resume_from_state=state)
