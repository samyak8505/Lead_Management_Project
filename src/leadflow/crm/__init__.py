from .base import CRMClient, DEAL_STAGES, DuplicateContactError
from .sqlite_adapter import SQLiteCRM

__all__ = ["CRMClient", "SQLiteCRM", "DEAL_STAGES", "DuplicateContactError"]
