"""Salesforce CRM Adapter implementing the CRMClient protocol.

Supports:
- Dual-mode: In-memory/sandbox mock mode for offline testing + live REST API mode.
- Standard Salesforce sObjects: Contact, Opportunity (Deal), Task (Interaction).
- SOQL queries, stage mapping, and idempotency guarantees.
"""
from __future__ import annotations
import json
import logging
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .base import CRMClient, DEAL_STAGES, DuplicateContactError

log = logging.getLogger("leadflow.crm.salesforce")

SF_STAGE_MAP = {
    "new": "Prospecting",
    "qualified": "Qualification",
    "proposal": "Proposal/Price Quote",
    "won": "Closed Won",
    "lost": "Closed Lost",
}

REVERSE_STAGE_MAP = {v: k for k, v in SF_STAGE_MAP.items()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SalesforceCRM(CRMClient):
    """Enterprise Salesforce adapter adhering to the CRMClient protocol."""

    def __init__(
        self,
        instance_url: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: str = "v58.0",
        is_sandbox: bool = True,
    ):
        self.instance_url = (instance_url or "https://login.salesforce.com").rstrip("/")
        self.access_token = access_token or "mock_sf_token"
        self.api_version = api_version
        self.is_sandbox = is_sandbox

        # In-memory sandbox storage for test/demo mode
        self._contacts: dict[int, dict] = {}
        self._deals: dict[int, dict] = {}
        self._interactions: dict[int, dict] = {}
        self._idempotency_keys: dict[str, dict] = {}
        self._audit_log: list[dict] = []
        self._counter: int = 1000

    def _next_id(self) -> int:
        self._counter += 1
        return self._counter

    # --- HTTP Helper for Live Mode ---
    def _sf_request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """Execute authenticated Salesforce REST API request."""
        url = f"{self.instance_url}/services/data/{self.api_version}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except Exception as e:
            log.error("Salesforce API error [%s %s]: %s", method, endpoint, e)
            raise RuntimeError(f"Salesforce API failed: {e}") from e

    # --- Reads ---
    def get_contact(self, contact_id: int) -> dict | None:
        if self.is_sandbox:
            c = self._contacts.get(contact_id)
            return dict(c) if c else None

        try:
            res = self._sf_request("GET", f"sobjects/Contact/{contact_id}")
            return {
                "id": contact_id,
                "first_name": res.get("FirstName"),
                "last_name": res.get("LastName"),
                "email": res.get("Email"),
                "phone": res.get("Phone"),
                "title": res.get("Title"),
                "company_name": res.get("Department") or res.get("Account", {}).get("Name"),
                "created_at": res.get("CreatedDate"),
            }
        except Exception:
            return None

    def find_by_email(self, email: str) -> list[dict]:
        norm = (email or "").strip().lower()
        if not norm:
            return []

        if self.is_sandbox:
            matches = []
            for c in self._contacts.values():
                if (c.get("email") or "").strip().lower() == norm:
                    matches.append(dict(c))
            return matches

        # SOQL Query
        query = f"SELECT Id, FirstName, LastName, Email, Phone, Title, CreatedDate FROM Contact WHERE Email = '{norm}'"
        encoded = urllib.parse.quote(query)
        res = self._sf_request("GET", f"query?q={encoded}")
        records = res.get("records", [])
        return [
            {
                "id": int(r["Id"]) if r["Id"].isdigit() else hash(r["Id"]) % 1000000,
                "first_name": r.get("FirstName"),
                "last_name": r.get("LastName"),
                "email": r.get("Email"),
                "phone": r.get("Phone"),
                "title": r.get("Title"),
                "created_at": r.get("CreatedDate"),
            }
            for r in records
        ]

    def search_contacts(self, name: str | None = None, company: str | None = None, limit: int = 20) -> list[dict]:
        if self.is_sandbox:
            res = []
            for c in self._contacts.values():
                match = False
                full_name = f"{c.get('first_name', '')} {c.get('last_name', '')}".lower()
                c_company = (c.get("company_name") or c.get("company") or "").lower()

                if name and name.lower() in full_name:
                    match = True
                if company and company.lower() in c_company:
                    match = True
                if match:
                    res.append(dict(c))
                if len(res) >= limit:
                    break
            return res

        where_clauses = []
        if name:
            where_clauses.append(f"Name LIKE '%{name}%'")
        if company:
            where_clauses.append(f"Department LIKE '%{company}%'")
        where_str = " AND ".join(where_clauses) if where_clauses else "Id != NULL"

        query = f"SELECT Id, FirstName, LastName, Email, Phone, Title, CreatedDate FROM Contact WHERE {where_str} LIMIT {limit}"
        encoded = urllib.parse.quote(query)
        data = self._sf_request("GET", f"query?q={encoded}")
        return [
            {
                "id": int(r["Id"]) if r["Id"].isdigit() else hash(r["Id"]) % 1000000,
                "first_name": r.get("FirstName"),
                "last_name": r.get("LastName"),
                "email": r.get("Email"),
                "phone": r.get("Phone"),
                "title": r.get("Title"),
                "created_at": r.get("CreatedDate"),
            }
            for r in data.get("records", [])
        ]

    def list_deals(self, contact_id: int) -> list[dict]:
        if self.is_sandbox:
            return [dict(d) for d in self._deals.values() if d.get("contact_id") == contact_id]

        query = f"SELECT Id, Name, Amount, StageName, CreatedDate FROM Opportunity WHERE ContactId = '{contact_id}'"
        encoded = urllib.parse.quote(query)
        data = self._sf_request("GET", f"query?q={encoded}")
        return [
            {
                "id": int(r["Id"]) if r["Id"].isdigit() else hash(r["Id"]) % 1000000,
                "contact_id": contact_id,
                "name": r.get("Name"),
                "amount": r.get("Amount"),
                "stage": REVERSE_STAGE_MAP.get(r.get("StageName"), "new"),
                "created_at": r.get("CreatedDate"),
            }
            for r in data.get("records", [])
        ]

    def list_interactions(self, contact_id: int, limit: int = 20) -> list[dict]:
        if self.is_sandbox:
            rows = [dict(i) for i in self._interactions.values() if i.get("contact_id") == contact_id]
            return sorted(rows, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]

        query = f"SELECT Id, Subject, Description, CreatedDate FROM Task WHERE WhoId = '{contact_id}' LIMIT {limit}"
        encoded = urllib.parse.quote(query)
        data = self._sf_request("GET", f"query?q={encoded}")
        return [
            {
                "id": int(r["Id"]) if r["Id"].isdigit() else hash(r["Id"]) % 1000000,
                "contact_id": contact_id,
                "kind": "email",
                "summary": r.get("Description") or r.get("Subject"),
                "created_at": r.get("CreatedDate"),
            }
            for r in data.get("records", [])
        ]

    # --- Writes ---
    def create_contact(
        self,
        fields: dict,
        idempotency_key: str | None = None,
        actor: str = "system",
        reject_duplicate_email: bool = False,
    ) -> dict:
        email = fields.get("email")
        if reject_duplicate_email and email:
            existing = self.find_by_email(email)
            if existing:
                raise DuplicateContactError([e["id"] for e in existing])

        if idempotency_key and idempotency_key in self._idempotency_keys:
            prev_id = self._idempotency_keys[idempotency_key]["id"]
            prev_c = self.get_contact(prev_id)
            if prev_c:
                prev_c["_replayed"] = True
                return prev_c

        now = _now()
        cid = self._next_id()
        contact = {
            "id": cid,
            "first_name": fields.get("first_name"),
            "last_name": fields.get("last_name") or "Lead",
            "email": fields.get("email"),
            "phone": fields.get("phone"),
            "title": fields.get("title") or fields.get("job_title"),
            "company_name": fields.get("company_name") or fields.get("company"),
            "created_at": now,
            "updated_at": now,
        }

        if self.is_sandbox:
            self._contacts[cid] = contact
            if idempotency_key:
                self._idempotency_keys[idempotency_key] = {"entity": "contact", "id": cid}
            self._audit_log.append({"actor": actor, "action": "create_contact", "id": cid, "ts": now})
            return dict(contact)

        # Live Salesforce REST insert
        sf_payload = {
            "FirstName": contact["first_name"],
            "LastName": contact["last_name"],
            "Email": contact["email"],
            "Phone": contact["phone"],
            "Title": contact["title"],
            "Department": contact["company_name"],
        }
        res = self._sf_request("POST", "sobjects/Contact", sf_payload)
        sf_id = res.get("id", str(cid))
        contact["salesforce_id"] = sf_id
        return contact

    def update_contact(self, contact_id: int, fields: dict, actor: str = "system") -> dict:
        c = self.get_contact(contact_id)
        if not c:
            raise KeyError(f"contact {contact_id} not found")

        now = _now()
        for k in ("first_name", "last_name", "email", "phone", "title", "company_name"):
            if k in fields and fields[k] is not None:
                c[k] = fields[k]
        c["updated_at"] = now

        if self.is_sandbox:
            self._contacts[contact_id] = c
            self._audit_log.append({"actor": actor, "action": "update_contact", "id": contact_id, "ts": now})
            return dict(c)

        sf_payload = {}
        if "first_name" in fields:
            sf_payload["FirstName"] = fields["first_name"]
        if "last_name" in fields:
            sf_payload["LastName"] = fields["last_name"]
        if "title" in fields:
            sf_payload["Title"] = fields["title"]
        self._sf_request("PATCH", f"sobjects/Contact/{contact_id}", sf_payload)
        return c

    def create_deal(
        self,
        contact_id: int,
        name: str,
        amount: float | None = None,
        stage: str = "new",
        idempotency_key: str | None = None,
        actor: str = "system",
    ) -> dict:
        if stage not in DEAL_STAGES:
            raise ValueError(f"stage must be one of {DEAL_STAGES}")

        if idempotency_key and idempotency_key in self._idempotency_keys:
            prev_id = self._idempotency_keys[idempotency_key]["id"]
            deal = self._deals.get(prev_id)
            if deal:
                res = dict(deal)
                res["_replayed"] = True
                return res

        did = self._next_id()
        now = _now()
        deal = {
            "id": did,
            "contact_id": contact_id,
            "name": name,
            "amount": amount,
            "stage": stage,
            "created_at": now,
            "updated_at": now,
        }

        if self.is_sandbox:
            self._deals[did] = deal
            if idempotency_key:
                self._idempotency_keys[idempotency_key] = {"entity": "deal", "id": did}
            self._audit_log.append({"actor": actor, "action": "create_deal", "id": did, "ts": now})
            return dict(deal)

        sf_stage = SF_STAGE_MAP.get(stage, "Prospecting")
        sf_payload = {
            "Name": name,
            "Amount": amount,
            "StageName": sf_stage,
            "CloseDate": datetime.now().strftime("%Y-%m-%d"),
            "ContactId": str(contact_id),
        }
        res = self._sf_request("POST", "sobjects/Opportunity", sf_payload)
        deal["salesforce_id"] = res.get("id")
        return deal

    def update_deal_stage(self, deal_id: int, stage: str, actor: str = "system") -> dict:
        if stage not in DEAL_STAGES:
            raise ValueError(f"stage must be one of {DEAL_STAGES}")

        if self.is_sandbox:
            deal = self._deals.get(deal_id)
            if not deal:
                raise KeyError(f"deal {deal_id} not found")
            deal["stage"] = stage
            deal["updated_at"] = _now()
            return dict(deal)

        sf_stage = SF_STAGE_MAP.get(stage, "Prospecting")
        self._sf_request("PATCH", f"sobjects/Opportunity/{deal_id}", {"StageName": sf_stage})
        deal = self._deals.get(deal_id, {"id": deal_id, "stage": stage})
        deal["stage"] = stage
        return deal

    def log_interaction(
        self,
        contact_id: int,
        kind: str,
        summary: str,
        idempotency_key: str | None = None,
        actor: str = "system",
    ) -> dict:
        if idempotency_key and idempotency_key in self._idempotency_keys:
            prev_id = self._idempotency_keys[idempotency_key]["id"]
            inter = self._interactions.get(prev_id)
            if inter:
                res = dict(inter)
                res["_replayed"] = True
                return res

        iid = self._next_id()
        now = _now()
        interaction = {
            "id": iid,
            "contact_id": contact_id,
            "kind": kind,
            "summary": summary,
            "created_at": now,
        }

        if self.is_sandbox:
            self._interactions[iid] = interaction
            if idempotency_key:
                self._idempotency_keys[idempotency_key] = {"entity": "interaction", "id": iid}
            self._audit_log.append({"actor": actor, "action": "log_interaction", "id": iid, "ts": now})
            return dict(interaction)

        sf_payload = {
            "Subject": f"LeadFlow {kind.capitalize()}",
            "Description": summary,
            "WhoId": str(contact_id),
            "Status": "Completed",
            "ActivityDate": datetime.now().strftime("%Y-%m-%d"),
        }
        res = self._sf_request("POST", "sobjects/Task", sf_payload)
        interaction["salesforce_id"] = res.get("id")
        return interaction

    def stats(self) -> dict:
        return {
            "provider": "salesforce",
            "is_sandbox": self.is_sandbox,
            "contacts": len(self._contacts),
            "deals": len(self._deals),
            "interactions": len(self._interactions),
        }
