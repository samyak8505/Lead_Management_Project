from __future__ import annotations
from typing import Any, Optional

from .base import CRMClient, DEAL_STAGES, DuplicateContactError
from .sqlite_adapter import SQLiteCRM
from .salesforce_adapter import SalesforceCRM
from .hubspot_adapter import HubSpotCRM

def get_crm_for_tenant(
    crm_config: Optional[Any] = None,
    db_path: str = "data/crm.db",
) -> CRMClient:
    """Instantiate appropriate CRM client based on tenant config."""
    if not crm_config:
        return SQLiteCRM(db_path)

    provider = getattr(crm_config, "provider", "sqlite")
    if provider == "salesforce":
        return SalesforceCRM(
            instance_url=getattr(crm_config, "instance_url", None),
            access_token=getattr(crm_config, "access_token", None),
            is_sandbox=getattr(crm_config, "is_sandbox", True),
        )
    elif provider == "hubspot":
        return HubSpotCRM(
            access_token=getattr(crm_config, "access_token", None),
            is_sandbox=getattr(crm_config, "is_sandbox", True),
        )
    return SQLiteCRM(db_path)

__all__ = [
    "CRMClient",
    "SQLiteCRM",
    "SalesforceCRM",
    "HubSpotCRM",
    "DEAL_STAGES",
    "DuplicateContactError",
    "get_crm_for_tenant",
]
