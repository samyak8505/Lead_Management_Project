"""Multi-tenant management and storage for LeadFlow SaaS."""
from __future__ import annotations
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from .models import Tenant, TenantCRMConfig, TenantSettings, generate_api_key, _now

TENANCY_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    website TEXT NOT NULL,
    industry TEXT NOT NULL,
    api_key TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_settings (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    settings_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_crm_config (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tenants_api_key ON tenants(api_key);
CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
"""


def _slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[-\s]+", "-", s)


class TenantManager:
    """Manages tenant workspaces, authentication API keys, ICP settings, and CRM configs."""

    def __init__(self, db_path: str | Path = "data/crm.db"):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(TENANCY_SCHEMA)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_tenant(
        self,
        name: str,
        website: str,
        industry: str = "B2B SaaS",
        slug: Optional[str] = None,
        api_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
        initial_settings: Optional[dict[str, Any]] = None,
        initial_crm: Optional[dict[str, Any]] = None,
    ) -> Tenant:
        """Create a new tenant organization with isolated API key, ICP, and CRM configs."""
        tid = tenant_id or f"ten_{uuid.uuid4().hex[:10]}"
        tslug = slug or _slugify(name)
        key = api_key or generate_api_key()
        now = _now()

        tenant = Tenant(
            id=tid,
            name=name,
            slug=tslug,
            website=website,
            industry=industry,
            api_key=key,
            created_at=now,
        )

        settings = TenantSettings(tenant_id=tid, **(initial_settings or {}))
        crm_config = TenantCRMConfig(tenant_id=tid, **(initial_crm or {}))

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO tenants (id, name, slug, website, industry, api_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tenant.id, tenant.name, tenant.slug, tenant.website, tenant.industry, tenant.api_key, tenant.created_at),
            )
            conn.execute(
                """INSERT INTO tenant_settings (tenant_id, settings_json, updated_at)
                   VALUES (?, ?, ?)""",
                (tenant.id, json.dumps(settings.to_dict()), now),
            )
            conn.execute(
                """INSERT INTO tenant_crm_config (tenant_id, config_json, updated_at)
                   VALUES (?, ?, ?)""",
                (tenant.id, json.dumps(crm_config.to_dict()), now),
            )

        return tenant

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Fetch tenant by ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            if not row:
                return None
            return Tenant(**dict(row))

    def get_tenant_by_api_key(self, api_key: str) -> Optional[Tenant]:
        """Authenticate tenant by API key."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM tenants WHERE api_key = ?", (api_key.strip(),)).fetchone()
            if not row:
                return None
            return Tenant(**dict(row))

    def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """Fetch tenant by URL slug."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM tenants WHERE slug = ?", (slug.strip().lower(),)).fetchone()
            if not row:
                return None
            return Tenant(**dict(row))

    def list_tenants(self) -> list[Tenant]:
        """List all registered tenants."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM tenants ORDER BY created_at DESC").fetchall()
            return [Tenant(**dict(r)) for r in rows]

    def get_settings(self, tenant_id: str) -> TenantSettings:
        """Fetch tenant's ICP, competitor, and tone settings."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT settings_json FROM tenant_settings WHERE tenant_id = ?", (tenant_id,)).fetchone()
            if not row:
                return TenantSettings(tenant_id=tenant_id)
            data = json.loads(row["settings_json"])
            return TenantSettings(**data)

    def update_settings(self, tenant_id: str, **kwargs) -> TenantSettings:
        """Update tenant's ICP and persona settings."""
        current = self.get_settings(tenant_id)
        d = current.to_dict()
        for k, v in kwargs.items():
            if k in d:
                d[k] = v
        d["tenant_id"] = tenant_id
        updated = TenantSettings(**d)

        now = _now()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO tenant_settings (tenant_id, settings_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tenant_id) DO UPDATE SET settings_json = excluded.settings_json, updated_at = excluded.updated_at""",
                (tenant_id, json.dumps(updated.to_dict()), now),
            )
        return updated

    def get_crm_config(self, tenant_id: str) -> TenantCRMConfig:
        """Fetch tenant's connected CRM configuration (Salesforce, HubSpot, SQLite)."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT config_json FROM tenant_crm_config WHERE tenant_id = ?", (tenant_id,)).fetchone()
            if not row:
                return TenantCRMConfig(tenant_id=tenant_id)
            data = json.loads(row["config_json"])
            return TenantCRMConfig(**data)

    def update_crm_config(self, tenant_id: str, **kwargs) -> TenantCRMConfig:
        """Update CRM provider, credentials, or deal stage mappings."""
        current = self.get_crm_config(tenant_id)
        d = current.to_dict()
        for k, v in kwargs.items():
            if k in d:
                d[k] = v
        d["tenant_id"] = tenant_id
        updated = TenantCRMConfig(**d)

        now = _now()
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO tenant_crm_config (tenant_id, config_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tenant_id) DO UPDATE SET config_json = excluded.config_json, updated_at = excluded.updated_at""",
                (tenant_id, json.dumps(updated.to_dict()), now),
            )
        return updated

    def rotate_api_key(self, tenant_id: str) -> str:
        """Generate and assign a new API key for the tenant."""
        new_key = generate_api_key()
        with self._get_conn() as conn:
            conn.execute("UPDATE tenants SET api_key = ? WHERE id = ?", (new_key, tenant_id))
        return new_key

    def ensure_default_tenant(self) -> Tenant:
        """Ensure a default demo tenant exists for local runs and tests."""
        existing = self.get_tenant("ten_default")
        if existing:
            return existing
        return self.create_tenant(
            name="LeadFlow Enterprise Demo",
            website="https://leadflow.ai",
            industry="B2B AI Software",
            slug="leadflow-demo",
            tenant_id="ten_default",
            api_key="lf_live_default_key_001",
            initial_settings={
                "target_industries": ["B2B SaaS", "Fintech", "Cybersecurity", "Healthcare"],
                "target_titles": ["VP Sales", "Head of RevOps", "Chief Revenue Officer", "CTO"],
                "min_budget": 15000.0,
                "min_seats": 10,
                "competitors": ["HubSpot", "Salesforce", "Apollo", "ZoomInfo"],
                "brand_pitch": "Automated, grounded sales lead qualification and email drafting.",
            },
        )
