import pytest
from leadflow.crm import SQLiteCRM
from leadflow.workflow.dedupe import dedupe, DedupeResult, match_first_name, match_last_name, match_company_name


@pytest.fixture
def crm_instance(tmp_path):
    crm = SQLiteCRM(tmp_path / "test_crm.db")
    # Create companies
    co1 = crm.create_company({"name": "Acme Corporation", "domain": "acme.com", "industry": "SaaS"}, actor="test")
    co2 = crm.create_company({"name": "Beta Health LLC", "domain": "betahealth.com", "industry": "Healthcare"}, actor="test")

    # Create contacts
    c1 = crm.create_contact({
        "first_name": "Robert",
        "last_name": "Johnson",
        "email": "robert.johnson@acme.com",
        "company_name": "Acme Corporation",
        "company_id": co1["id"],
    }, actor="test")

    c2 = crm.create_contact({
        "first_name": "Alice",
        "last_name": "Smith",
        "email": "alice.smith@betahealth.com",
        "company_name": "Beta Health LLC",
        "company_id": co2["id"],
    }, actor="test")

    # Create a duplicate contact of Robert in same company
    crm.create_contact({
        "first_name": "Bob",
        "last_name": "Johnson",
        "email": "b.johnson@acme.com",
        "company_name": "Acme Corp",
        "company_id": co1["id"],
    }, actor="test")

    return crm, c1["id"], c2["id"]


def test_exact_email_match(crm_instance):
    crm, c1_id, _ = crm_instance
    ext = {
        "first_name": "Robert",
        "last_name": "Johnson",
        "email": "  ROBERT.JOHNSON@ACME.COM \n",
        "company_name": "Acme Corporation"
    }
    res = dedupe(ext, crm)
    assert res.decision == "existing"
    assert c1_id in res.match_ids
    assert res.confidence == 1.0
    assert "exact_email_match" in res.reasons


def test_fuzzy_nickname_and_same_domain(crm_instance):
    crm, c1_id, _ = crm_instance
    # "Bob" instead of "Robert", new email style "bjohnson@acme.com" at same domain
    ext = {
        "first_name": "Bob",
        "last_name": "Johnson",
        "email": "bjohnson@acme.com",
        "company_name": "Acme Corp"
    }
    res = dedupe(ext, crm)
    assert res.decision == "existing"
    assert c1_id in res.match_ids
    assert res.confidence >= 0.9
    assert "name_or_nickname_and_company_domain_match" in res.reasons


def test_domain_change_needs_review(crm_instance):
    crm, c1_id, _ = crm_instance
    # Same person & company name, but new unseen email domain
    ext = {
        "first_name": "Robert",
        "last_name": "Johnson",
        "email": "robert@acme-newdomain.io",
        "company_name": "Acme Corporation"
    }
    res = dedupe(ext, crm)
    assert res.decision == "needs_review"
    assert c1_id in res.match_ids
    assert "name_and_company_match_with_new_or_missing_email_domain" in res.reasons


def test_web_form_no_email_needs_review(crm_instance):
    crm, _, c2_id = crm_instance
    # Web form submission with no email provided
    ext = {
        "first_name": "Alice",
        "last_name": "Smith",
        "email": None,
        "company_name": "Beta Health"
    }
    res = dedupe(ext, crm)
    assert res.decision == "needs_review"
    assert c2_id in res.match_ids
    assert "name_and_company_match_with_new_or_missing_email_domain" in res.reasons


def test_name_collision_guard_stays_new(crm_instance):
    crm, c1_id, _ = crm_instance
    # Same name "Robert Johnson", but at a completely different company and domain
    ext = {
        "first_name": "Robert",
        "last_name": "Johnson",
        "email": "robert.johnson@unrelatedfirm.org",
        "company_name": "Unrelated Firm Inc"
    }
    res = dedupe(ext, crm)
    # Must NOT merge into existing! Zero false merges guard
    assert res.decision == "new"
    assert res.match_ids == []


def test_unseen_lead_is_new(crm_instance):
    crm, _, _ = crm_instance
    ext = {
        "first_name": "Samantha",
        "last_name": "Vance",
        "email": "samantha.vance@innovate-tech.co",
        "company_name": "Innovate Tech"
    }
    res = dedupe(ext, crm)
    assert res.decision == "new"
    assert res.match_ids == []


def test_empty_lead_graceful_handling(crm_instance):
    crm, _, _ = crm_instance
    ext = {"first_name": None, "last_name": None, "email": None, "company_name": None}
    res = dedupe(ext, crm)
    assert res.decision == "new"
    assert res.match_ids == []


def test_name_and_company_matchers():
    assert match_first_name("Robert", "Bob") is True
    assert match_first_name("Jen", "Jennifer") is True
    assert match_first_name("Christopher", "Chris") is True
    assert match_first_name("John", "Zachary") is False
    assert match_last_name("Johnson", "Johnson") is True
    assert match_last_name("Smith", "Smyth") is True
    assert match_company_name("Acme Corp.", "Acme Corporation") is True
    assert match_company_name("Beta Health LLC", "Beta Health Inc") is True
    assert match_company_name("Beta Health", "Totally Different Co") is False
