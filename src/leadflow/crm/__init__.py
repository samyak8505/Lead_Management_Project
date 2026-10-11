from .base import CRMClient, DEAL_STAGES, DuplicateContactError
from .sqlite_adapter import SQLiteCRM
from .salesforce_adapter import SalesforceCRM

__all__ = ["CRMClient", "SQLiteCRM", "SalesforceCRM", "DEAL_STAGES", "DuplicateContactError"]
