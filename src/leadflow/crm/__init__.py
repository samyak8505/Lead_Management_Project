from .base import CRMClient, DEAL_STAGES
from .sqlite_adapter import SQLiteCRM

__all__ = ["CRMClient", "SQLiteCRM", "DEAL_STAGES"]
