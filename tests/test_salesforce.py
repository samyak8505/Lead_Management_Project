"""Phase 1: Salesforce CRM Adapter Unit Tests."""
from __future__ import annotations
import pytest
from leadflow.crm import SalesforceCRM, CRMClient, DuplicateContactError


@pytest.fixture
def sf():
    return SalesforceCRM(
        instance_url="https://test.salesforce.com",
        access_token="mock_token_xyz",
        is_sandbox=True,
    )


def test_salesforce_conforms_to_crm_client_protocol(sf):
    assert isinstance(sf, CRMClient)


def test_salesforce_create_and_find_contact(sf):
    contact = sf.create_contact({
        "first_name": "Elena",
        "last_name": "Rostova",
        "email": "elena.rostova@cybertech.org",
        "title": "Chief Security Officer",
        "company_name": "CyberTech Global",
    })
    assert contact["id"] is not None
    assert contact["first_name"] == "Elena"
    assert contact["last_name"] == "Rostova"
    assert contact["company_name"] == "CyberTech Global"

    # Find by email (case-insensitive)
    found = sf.find_by_email("  ELENA.ROSTOVA@CYBERTECH.ORG ")
    assert len(found) == 1
    assert found[0]["id"] == contact["id"]

    # Search contacts
    results = sf.search_contacts(name="Elena")
    assert len(results) == 1
    assert results[0]["id"] == contact["id"]

    results_company = sf.search_contacts(company="CyberTech")
    assert len(results_company) == 1
    assert results_company[0]["id"] == contact["id"]


def test_salesforce_reject_duplicate_email(sf):
    sf.create_contact({"email": "dup@test.com", "first_name": "Alice"})
    with pytest.raises(DuplicateContactError) as exc:
        sf.create_contact({"email": "dup@test.com", "first_name": "Bob"}, reject_duplicate_email=True)
    assert len(exc.value.existing_ids) == 1


def test_salesforce_update_contact(sf):
    c = sf.create_contact({"email": "update@test.com", "first_name": "John", "title": "Developer"})
    updated = sf.update_contact(c["id"], {"title": "VP Engineering", "phone": "+1 555-0199"})
    assert updated["title"] == "VP Engineering"
    assert updated["phone"] == "+1 555-0199"

    # Verify through get_contact
    fresh = sf.get_contact(c["id"])
    assert fresh["title"] == "VP Engineering"


def test_salesforce_create_and_update_deal(sf):
    c = sf.create_contact({"email": "deal@test.com", "first_name": "Dealmaker"})
    deal = sf.create_deal(
        contact_id=c["id"],
        name="Enterprise Security 500 Seats",
        amount=45000.0,
        stage="new",
    )
    assert deal["id"] is not None
    assert deal["contact_id"] == c["id"]
    assert deal["amount"] == 45000.0
    assert deal["stage"] == "new"

    deals = sf.list_deals(c["id"])
    assert len(deals) == 1
    assert deals[0]["id"] == deal["id"]

    # Update stage to won
    updated = sf.update_deal_stage(deal["id"], "won")
    assert updated["stage"] == "won"

    # Invalid stage rejected
    with pytest.raises(ValueError):
        sf.update_deal_stage(deal["id"], "non_existent_stage")


def test_salesforce_log_interaction(sf):
    c = sf.create_contact({"email": "task@test.com", "first_name": "TaskUser"})
    inter = sf.log_interaction(
        contact_id=c["id"],
        kind="email",
        summary="Sent initial architecture demo invitation.",
    )
    assert inter["id"] is not None
    assert inter["contact_id"] == c["id"]

    tasks = sf.list_interactions(c["id"])
    assert len(tasks) == 1
    assert "architecture demo" in tasks[0]["summary"]


def test_salesforce_idempotency_prevents_duplicates(sf):
    c1 = sf.create_contact({"email": "idem@test.com", "first_name": "Idem"}, idempotency_key="key-001")
    c2 = sf.create_contact({"email": "idem@test.com", "first_name": "Idem"}, idempotency_key="key-001")
    assert c1["id"] == c2["id"]
    assert c2.get("_replayed") is True

    d1 = sf.create_deal(contact_id=c1["id"], name="Deal 1", amount=10000, idempotency_key="deal-key-01")
    d2 = sf.create_deal(contact_id=c1["id"], name="Deal 1", amount=10000, idempotency_key="deal-key-01")
    assert d1["id"] == d2["id"]
    assert d2.get("_replayed") is True


def test_salesforce_stats(sf):
    sf.create_contact({"email": "s1@test.com", "first_name": "Stat1"})
    sf.create_deal(contact_id=1001, name="D1", amount=5000)
    sf.log_interaction(contact_id=1001, kind="call", summary="Call held")

    stats = sf.stats()
    assert stats["provider"] == "salesforce"
    assert stats["contacts"] == 1
    assert stats["deals"] == 1
    assert stats["interactions"] == 1
