import pytest
from leadflow.crm import SQLiteCRM
from leadflow.workflow.dedupe import DedupeResult
from leadflow.workflow.scoring import score_lead, _lookup_company, _lookup_history, _evaluate_signals


@pytest.fixture
def crm_instance(tmp_path):
    crm = SQLiteCRM(tmp_path / "test_crm.db")
    co = crm.create_company({"name": "Apex Enterprise", "domain": "apex.com", "employee_count": 500}, actor="test")
    c = crm.create_contact({
        "first_name": "Marcus",
        "last_name": "Vance",
        "email": "marcus@apex.com",
        "company_id": co["id"],
    }, actor="test")
    crm.create_deal(c["id"], name="Apex Pilot", amount=50000, stage="won", actor="test")
    crm.log_interaction(c["id"], kind="call", summary="Discovery call completed", actor="test")
    return crm, c["id"]


def test_parallel_enrichment_subtasks(crm_instance):
    crm, c_id = crm_instance
    co_info = _lookup_company("Apex Enterprise", crm)
    assert co_info["employee_count"] == 500
    assert co_info["source"] == "crm_database"

    dedupe_res = DedupeResult(decision="existing", match_ids=[c_id])
    history = _lookup_history(dedupe_res, crm)
    assert history["past_deals"] == 1
    assert history["won_deals"] == 1
    assert history["is_existing_customer"] is True

    raw = "Hello, can we get pricing? We have an approved budget of $30,000."
    ext = {
        "job_title": "VP of Engineering",
        "intent": "pricing_inquiry",
        "urgency": "high",
        "budget_signal": "explicit",
    }
    signals = _evaluate_signals(ext, raw)
    assert signals["is_decision_maker"] is True
    assert signals["has_concrete_ask"] is True
    assert signals["is_high_urgency"] is True
    assert signals["is_explicit_budget"] is True


def test_hot_tier_lead(crm_instance):
    crm, _ = crm_instance
    raw = "Hi, we want a demo of your enterprise plan for 200 users this week. Budget is $40k."
    ext = {
        "first_name": "Sarah",
        "last_name": "Connor",
        "job_title": "Chief Technology Officer",
        "company_name": "Apex Enterprise",
        "intent": "demo_request",
        "urgency": "high",
        "budget_signal": "explicit",
    }
    res = score_lead(raw, ext, crm=crm)
    assert res.tier == "hot"
    assert res.score >= 75
    assert any("decision_maker" in r for r in res.reasons)
    assert any("concrete_ask" in r for r in res.reasons)
    assert res.rationale != ""


def test_partnership_inquiry_is_warm():
    raw = "Hello, we are interested in exploring a reseller partnership with your company."
    ext = {
        "first_name": "David",
        "last_name": "Miller",
        "job_title": "Head of Partnerships",
        "company_name": "Channel Partners Inc",
        "intent": "partnership",
        "urgency": "low",
        "budget_signal": "none",
    }
    res = score_lead(raw, ext)
    assert res.tier == "warm"
    assert any("partnership" in r for r in res.reasons)


def test_early_research_is_cold():
    raw = "I am a student writing a paper on CRM software. Do you have a whitepaper or case study I could read?"
    ext = {
        "first_name": "Emily",
        "last_name": "Stone",
        "job_title": None,
        "company_name": "State University",
        "intent": "general_question",
        "urgency": "low",
        "budget_signal": "none",
    }
    res = score_lead(raw, ext)
    assert res.tier == "cold"
    assert "early_stage_research_inquiry" in res.reasons


def test_non_sales_intents_are_tier_none():
    for non_sales in ("support_request", "spam", "vendor_pitch", "unclear"):
        ext = {"intent": non_sales, "urgency": "low", "budget_signal": "none", "job_title": "CEO"}
        res = score_lead("Sample text", ext)
        assert res.tier == "none"
        assert res.score == 0
