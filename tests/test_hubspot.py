"""Phase 4: HubSpot CRM Adapter Unit Tests."""
from __future__ import annotations
import pytest
from leadflow.crm import HubSpotCRM, CRMClient, DuplicateContactError


@pytest.fixture
def hs():
    return HubSpotCRM(
        access_token="mock_hubspot_token_abc",
        is_sandbox=True,
    )


def test_hubspot_conforms_to_crm_client_protocol(hs):
    assert isinstance(hs, CRMClient)


def test_hubspot_create_and_find_contact(hs):
    contact = hs.create_contact({
        "first_name": "Marcus",
        "last_name": "Vance",
        "email": "marcus.vance@revops.tech",
        "title": "VP Revenue Operations",
        "company_name": "RevOps Tech",
    })
    assert contact["id"] is not None
    assert contact["first_name"] == "Marcus"
    assert contact["last_name"] == "Vance"
    assert contact["company_name"] == "RevOps Tech"

    # Find by email (case-insensitive)
    found = hs.find_by_email(" MARCUS.VANCE@REVOPS.TECH ")
    assert len(found) == 1
    assert found[0]["id"] == contact["id"]

    # Search contacts
    results = hs.search_contacts(name="Marcus")
    assert len(results) == 1
    assert results[0]["id"] == contact["id"]

    results_company = hs.search_contacts(company="RevOps")
    assert len(results_company) == 1
    assert results_company[0]["id"] == contact["id"]


def test_hubspot_reject_duplicate_email(hs):
    hs.create_contact({"email": "dup@hubspot.test", "first_name": "Carol"})
    with pytest.raises(DuplicateContactError) as exc:
        hs.create_contact({"email": "dup@hubspot.test", "first_name": "Dave"}, reject_duplicate_email=True)
    assert len(exc.value.existing_ids) == 1


def test_hubspot_update_contact(hs):
    c = hs.create_contact({"email": "update@hubspot.test", "first_name": "Frank", "title": "Analyst"})
    updated = hs.update_contact(c["id"], {"title": "Director of Strategy", "phone": "+1 555-4321"})
    assert updated["title"] == "Director of Strategy"
    assert updated["phone"] == "+1 555-4321"


def test_hubspot_create_and_update_deal(hs):
    c = hs.create_contact({"email": "deal@hubspot.test", "first_name": "Grace"})
    deal = hs.create_deal(c["id"], name="Grace Corp - Enterprise Expansion", amount=45000.0, stage="new")
    assert deal["id"] is not None
    assert deal["stage"] == "new"
    assert deal["amount"] == 45000.0

    # List deals
    deals = hs.list_deals(c["id"])
    assert len(deals) == 1
    assert deals[0]["id"] == deal["id"]

    # Update deal stage
    updated_deal = hs.update_deal_stage(deal["id"], stage="qualified")
    assert updated_deal["stage"] == "qualified"

    # Invalid stage should raise ValueError
    with pytest.raises(ValueError):
        hs.update_deal_stage(deal["id"], stage="non_existent_stage")


def test_hubspot_log_interaction(hs):
    c = hs.create_contact({"email": "interaction@hubspot.test", "first_name": "Heidi"})
    interaction = hs.log_interaction(c["id"], kind="email", summary="Outbound pitch email dispatched")
    assert interaction["id"] is not None
    assert interaction["kind"] == "email"

    interactions = hs.list_interactions(c["id"])
    assert len(interactions) == 1
    assert interactions[0]["id"] == interaction["id"]


def test_hubspot_idempotency_guarantees(hs):
    c1 = hs.create_contact({"email": "idemp@hubspot.test"}, idempotency_key="idemp-key-1")
    c2 = hs.create_contact({"email": "idemp@hubspot.test"}, idempotency_key="idemp-key-1")
    assert c1["id"] == c2["id"]

    d1 = hs.create_deal(c1["id"], name="Idemp Deal", amount=10000.0, idempotency_key="idemp-deal-1")
    d2 = hs.create_deal(c1["id"], name="Idemp Deal", amount=10000.0, idempotency_key="idemp-deal-1")
    assert d1["id"] == d2["id"]
