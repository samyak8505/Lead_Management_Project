"""CRM-agnostic interface. The workflow only ever talks to this.
Adapters: SQLiteCRM (Step 2), SalesforceCRM / HubSpotCRM (later)."""
from typing import Protocol

DEAL_STAGES = ("new", "qualified", "proposal", "won", "lost")


class DuplicateContactError(Exception):
    """Raised when create_contact(reject_duplicate_email=True) finds an exact email match."""

    def __init__(self, existing_ids: list[int]):
        self.existing_ids = existing_ids
        super().__init__(f"contact with this email already exists: {existing_ids}")


class CRMClient(Protocol):
    # --- reads (safe, no side effects) ---
    def get_contact(self, contact_id: int) -> dict | None: ...
    def find_by_email(self, email: str) -> list[dict]: ...
    def search_contacts(self, name: str | None = None, company: str | None = None,
                        limit: int = 20) -> list[dict]: ...
    def list_interactions(self, contact_id: int, limit: int = 20) -> list[dict]: ...
    def list_deals(self, contact_id: int) -> list[dict]: ...

    # --- writes (always audited; idempotent when a key is given) ---
    def create_contact(self, fields: dict, idempotency_key: str | None = None,
                       actor: str = "system", reject_duplicate_email: bool = False) -> dict: ...
    def update_contact(self, contact_id: int, fields: dict, actor: str = "system") -> dict: ...
    def create_deal(self, contact_id: int, name: str, amount: float | None = None,
                    stage: str = "new", idempotency_key: str | None = None,
                    actor: str = "system") -> dict: ...
    def update_deal_stage(self, deal_id: int, stage: str, actor: str = "system") -> dict: ...
    def log_interaction(self, contact_id: int, kind: str, summary: str,
                        idempotency_key: str | None = None, actor: str = "system") -> dict: ...
