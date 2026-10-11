"""Multi-tenant data models and configurations for LeadFlow SaaS."""
from __future__ import annotations
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_api_key(prefix: str = "lf_live_") -> str:
    """Generate secure cryptographically random tenant API key."""
    return f"{prefix}{secrets.token_hex(16)}"


@dataclass
class Tenant:
    id: str
    name: str
    slug: str
    website: str
    industry: str
    api_key: str
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TenantSettings:
    tenant_id: str
    target_industries: list[str] = field(default_factory=lambda: ["Technology", "Software", "Fintech"])
    target_titles: list[str] = field(default_factory=lambda: ["CTO", "VP Engineering", "Head of IT", "Director"])
    min_budget: float = 10000.0
    min_seats: int = 10
    competitors: list[str] = field(default_factory=lambda: ["Salesforce", "HubSpot", "Apollo"])
    brand_pitch: str = "Enterprise-grade lead qualification, scoring, and drafting in seconds."
    tone: str = "consultative"  # consultative | executive | technical | friendly
    auto_approve_hot: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TenantCRMConfig:
    tenant_id: str
    provider: Literal["sqlite", "salesforce", "hubspot"] = "sqlite"
    instance_url: Optional[str] = None
    access_token: Optional[str] = None
    default_deal_stage: str = "new"
    is_sandbox: bool = True
    custom_fields_mapping: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
