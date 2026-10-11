"""Phase 1: Multi-Tenancy Foundation Unit Tests."""
from __future__ import annotations
import pytest
from leadflow.tenancy import TenantManager, TenantSettings, TenantCRMConfig


@pytest.fixture
def manager(tmp_path):
    db_file = tmp_path / "tenants_test.db"
    return TenantManager(db_file)


def test_create_and_fetch_tenant(manager):
    tenant = manager.create_tenant(
        name="CyberShield Security",
        website="https://cybershield.io",
        industry="Cybersecurity",
    )
    assert tenant.id.startswith("ten_")
    assert tenant.name == "CyberShield Security"
    assert tenant.slug == "cybershield-security"
    assert tenant.api_key.startswith("lf_live_")

    # Fetch by ID
    fetched = manager.get_tenant(tenant.id)
    assert fetched is not None
    assert fetched.name == tenant.name

    # Fetch by API Key
    auth_tenant = manager.get_tenant_by_api_key(tenant.api_key)
    assert auth_tenant is not None
    assert auth_tenant.id == tenant.id

    # Fetch by Slug
    slug_tenant = manager.get_tenant_by_slug("cybershield-security")
    assert slug_tenant is not None
    assert slug_tenant.id == tenant.id


def test_tenant_settings_and_icp(manager):
    tenant = manager.create_tenant(
        name="FinFlow Corp",
        website="https://finflow.com",
        industry="Fintech",
        initial_settings={
            "target_industries": ["Banking", "Insurance"],
            "target_titles": ["CFO", "VP Finance"],
            "min_budget": 50000.0,
            "competitors": ["Stripe", "Adyen"],
            "brand_pitch": "Automated payment reconciliation for enterprise banks.",
        },
    )

    settings = manager.get_settings(tenant.id)
    assert "Banking" in settings.target_industries
    assert "CFO" in settings.target_titles
    assert settings.min_budget == 50000.0
    assert "Stripe" in settings.competitors

    # Update settings
    updated = manager.update_settings(
        tenant.id,
        min_budget=75000.0,
        competitors=["Stripe", "Adyen", "Checkout.com"],
    )
    assert updated.min_budget == 75000.0
    assert "Checkout.com" in updated.competitors

    # Verify persistence
    persisted = manager.get_settings(tenant.id)
    assert persisted.min_budget == 75000.0


def test_tenant_crm_config(manager):
    tenant = manager.create_tenant(
        name="Apex Logistics",
        website="https://apex.com",
        industry="Logistics",
    )

    default_config = manager.get_crm_config(tenant.id)
    assert default_config.provider == "sqlite"

    # Switch to Salesforce CRM
    updated_crm = manager.update_crm_config(
        tenant.id,
        provider="salesforce",
        instance_url="https://apex.my.salesforce.com",
        access_token="sf_oauth_token_123",
        default_deal_stage="qualified",
    )
    assert updated_crm.provider == "salesforce"
    assert updated_crm.instance_url == "https://apex.my.salesforce.com"
    assert updated_crm.default_deal_stage == "qualified"

    persisted_crm = manager.get_crm_config(tenant.id)
    assert persisted_crm.provider == "salesforce"


def test_api_key_rotation(manager):
    tenant = manager.create_tenant(name="Key Rotate Co", website="https://key.com")
    old_key = tenant.api_key

    new_key = manager.rotate_api_key(tenant.id)
    assert new_key != old_key
    assert new_key.startswith("lf_live_")

    # Old key fails authentication
    assert manager.get_tenant_by_api_key(old_key) is None
    # New key succeeds
    assert manager.get_tenant_by_api_key(new_key) is not None


def test_ensure_default_tenant(manager):
    default_t = manager.ensure_default_tenant()
    assert default_t.id == "ten_default"
    assert default_t.name == "LeadFlow Enterprise Demo"

    # Repeated call returns same tenant without error
    second = manager.ensure_default_tenant()
    assert second.id == default_t.id
