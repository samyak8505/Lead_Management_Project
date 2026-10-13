"""HubSpot CRM Adapter implementing the CRMClient protocol.

Supports:
- Dual-mode: In-memory/sandbox mock mode for offline testing + live REST API mode.
- Standard HubSpot objects: Contacts, Deals, Engagements/Notes.
- Stage mapping (new -> appointmentscheduled, qualified -> qualifiedtobuy, etc.).
- Idempotency guarantees and full audit logging.
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

log = logging.getLogger("leadflow.crm.hubspot")

HUBSPOT_STAGE_MAP = {
    "new": "appointmentscheduled",
    "qualified": "qualifiedtobuy",
    "proposal": "presentationscheduled",
    "won": "closedwon",
    "lost": "closedlost",
}

REVERSE_HUBSPOT_STAGE_MAP = {v: k for k, v in HUBSPOT_STAGE_MAP.items()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HubSpotCRM(CRMClient):
    """Enterprise HubSpot adapter adhering to the CRMClient protocol."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        base_url: str = "https://api.hubapi.com",
        is_sandbox: bool = True,
    ):
        self.access_token = access_token or "mock_hubspot_token"
        self.base_url = base_url.rstrip("/")
        self.is_sandbox = is_sandbox

        # In-memory sandbox storage for test/demo mode
        self._contacts: dict[int, dict] = {}
        self._deals: dict[int, dict] = {}
        self._interactions: dict[int, dict] = {}
        self._idempotency_keys: dict[str, dict] = {}
        self._audit_log: list[dict] = []
        self._counter: int = 5000

    def _next_id(self) -> int:
        self._counter += 1
        return self._counter

    def _record_audit(self, action: str, entity_type: str, entity_id: int, details: dict, actor: str = "system") -> None:
        self._audit_log.append({
            "timestamp": _now(),
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "actor": actor,
            "details": details,
        })

    def _hs_request(self, method: str, endpoint: str, payload: Optional[dict] = None) -> dict:
        """Execute HTTP request against HubSpot REST API."""
        if self.is_sandbox:
            return {}

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        data_bytes = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                res_body = resp.read().decode("utf-8")
                return json.loads(res_body) if res_body else {}
        except Exception as e:
            log.error("HubSpot API request error [%s %s]: %s", method, endpoint, e)
            raise

    # ---------------- CRMClient Reads ----------------

    def get_contact(self, contact_id: int) -> dict | None:
        if self.is_sandbox:
            c = self._contacts.get(contact_id)
            return dict(c) if c else None

        try:
            res = self._hs_request("GET", f"crm/v3/objects/contacts/{contact_id}?properties=firstname,lastname,email,phone,jobtitle,company")
            props = res.get("properties", {})
            return {
                "id": contact_id,
                "first_name": props.get("firstname"),
                "last_name": props.get("lastname"),
                "email": props.get("email"),
                "phone": props.get("phone"),
                "title": props.get("jobtitle"),
                "company_name": props.get("company"),
                "created_at": res.get("createdAt"),
            }
        except Exception:
            return None

    def find_by_email(self, email: str) -> list[dict]:
        email_clean = (email or "").strip().lower()
        if not email_clean:
            return []

        if self.is_sandbox:
            return [dict(c) for c in self._contacts.values() if (c.get("email") or "").lower() == email_clean]

        payload = {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": email_clean,
                }]
            }],
            "properties": ["firstname", "lastname", "email", "phone", "jobtitle", "company"],
        }
        res = self._hs_request("POST", "crm/v3/objects/contacts/search", payload)
        results = res.get("results", [])
        return [
            {
                "id": int(r["id"]) if r["id"].isdigit() else hash(r["id"]) % 1000000,
                "first_name": r.get("properties", {}).get("firstname"),
                "last_name": r.get("properties", {}).get("lastname"),
                "email": r.get("properties", {}).get("email"),
                "phone": r.get("properties", {}).get("phone"),
                "title": r.get("properties", {}).get("jobtitle"),
                "created_at": r.get("createdAt"),
            }
            for r in results
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

        query = name or company or ""
        payload = {
            "query": query,
            "limit": limit,
            "properties": ["firstname", "lastname", "email", "phone", "jobtitle", "company"],
        }
        res = self._hs_request("POST", "crm/v3/objects/contacts/search", payload)
        return [
            {
                "id": int(r["id"]) if r["id"].isdigit() else hash(r["id"]) % 1000000,
                "first_name": r.get("properties", {}).get("firstname"),
                "last_name": r.get("properties", {}).get("lastname"),
                "email": r.get("properties", {}).get("email"),
                "phone": r.get("properties", {}).get("phone"),
                "title": r.get("properties", {}).get("jobtitle"),
                "company_name": r.get("properties", {}).get("company"),
                "created_at": r.get("createdAt"),
            }
            for r in res.get("results", [])
        ]

    def list_deals(self, contact_id: int) -> list[dict]:
        if self.is_sandbox:
            return [dict(d) for d in self._deals.values() if d.get("contact_id") == contact_id]

        res = self._hs_request("GET", f"crm/v3/objects/contacts/{contact_id}/associations/deals")
        deal_ids = [d["id"] for d in res.get("results", [])]
        deals = []
        for did in deal_ids:
            d_res = self._hs_request("GET", f"crm/v3/objects/deals/{did}?properties=dealname,amount,dealstage")
            props = d_res.get("properties", {})
            raw_stage = props.get("dealstage", "appointmentscheduled")
            stage = REVERSE_HUBSPOT_STAGE_MAP.get(raw_stage, "new")
            deals.append({
                "id": int(did) if str(did).isdigit() else hash(did) % 1000000,
                "contact_id": contact_id,
                "name": props.get("dealname"),
                "amount": float(props.get("amount", 0.0)) if props.get("amount") else None,
                "stage": stage,
                "created_at": d_res.get("createdAt"),
            })
        return deals

    def list_interactions(self, contact_id: int, limit: int = 20) -> list[dict]:
        if self.is_sandbox:
            items = [dict(i) for i in self._interactions.values() if i.get("contact_id") == contact_id]
            return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]

        return []

    # ---------------- CRMClient Writes ----------------

    def create_contact(
        self,
        fields: dict,
        idempotency_key: str | None = None,
        actor: str = "system",
        reject_duplicate_email: bool = False,
    ) -> dict:
        if idempotency_key and idempotency_key in self._idempotency_keys:
            return dict(self._idempotency_keys[idempotency_key])

        email = fields.get("email")
        if email:
            existing = self.find_by_email(email)
            if existing and reject_duplicate_email:
                raise DuplicateContactError([e["id"] for e in existing])

        cid = self._next_id()
        now = _now()
        contact = {
            "id": cid,
            "first_name": fields.get("first_name"),
            "last_name": fields.get("last_name"),
            "email": fields.get("email"),
            "phone": fields.get("phone"),
            "title": fields.get("title"),
            "company_name": fields.get("company_name") or fields.get("company"),
            "created_at": now,
            "updated_at": now,
        }

        if self.is_sandbox:
            self._contacts[cid] = dict(contact)
        else:
            payload = {
                "properties": {
                    "firstname": fields.get("first_name", ""),
                    "lastname": fields.get("last_name", ""),
                    "email": fields.get("email", ""),
                    "phone": fields.get("phone", ""),
                    "jobtitle": fields.get("title", ""),
                    "company": fields.get("company_name", ""),
                }
            }
            res = self._hs_request("POST", "crm/v3/objects/contacts", payload)
            if "id" in res:
                contact["id"] = int(res["id"]) if res["id"].isdigit() else cid

        self._record_audit("create_contact", "contact", contact["id"], fields, actor)

        if idempotency_key:
            self._idempotency_keys[idempotency_key] = dict(contact)

        return contact

    def update_contact(self, contact_id: int, fields: dict, actor: str = "system") -> dict:
        if self.is_sandbox:
            if contact_id not in self._contacts:
                raise KeyError(f"Contact ID {contact_id} not found")
            contact = self._contacts[contact_id]
            contact.update(fields)
            contact["updated_at"] = _now()
            self._record_audit("update_contact", "contact", contact_id, fields, actor)
            return dict(contact)

        payload = {
            "properties": {
                k: str(v) for k, v in fields.items() if k in ["firstname", "lastname", "phone", "jobtitle", "company"]
            }
        }
        self._hs_request("PATCH", f"crm/v3/objects/contacts/{contact_id}", payload)
        self._record_audit("update_contact", "contact", contact_id, fields, actor)
        return self.get_contact(contact_id) or {"id": contact_id, **fields}

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
            raise ValueError(f"Invalid deal stage '{stage}'. Must be one of {DEAL_STAGES}")

        if idempotency_key and idempotency_key in self._idempotency_keys:
            return dict(self._idempotency_keys[idempotency_key])

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
            self._deals[did] = dict(deal)
        else:
            hs_stage = HUBSPOT_STAGE_MAP.get(stage, "appointmentscheduled")
            payload = {
                "properties": {
                    "dealname": name,
                    "amount": str(amount or 0.0),
                    "dealstage": hs_stage,
                },
                "associations": [{
                    "to": {"id": str(contact_id)},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 3}],
                }],
            }
            res = self._hs_request("POST", "crm/v3/objects/deals", payload)
            if "id" in res:
                deal["id"] = int(res["id"]) if res["id"].isdigit() else did

        self._record_audit("create_deal", "deal", deal["id"], {"contact_id": contact_id, "name": name, "amount": amount, "stage": stage}, actor)

        if idempotency_key:
            self._idempotency_keys[idempotency_key] = dict(deal)

        return deal

    def update_deal_stage(self, deal_id: int, stage: str, actor: str = "system") -> dict:
        if stage not in DEAL_STAGES:
            raise ValueError(f"Invalid deal stage '{stage}'. Must be one of {DEAL_STAGES}")

        if self.is_sandbox:
            if deal_id not in self._deals:
                raise KeyError(f"Deal ID {deal_id} not found")
            deal = self._deals[deal_id]
            deal["stage"] = stage
            deal["updated_at"] = _now()
            self._record_audit("update_deal_stage", "deal", deal_id, {"stage": stage}, actor)
            return dict(deal)

        hs_stage = HUBSPOT_STAGE_MAP.get(stage, "appointmentscheduled")
        payload = {"properties": {"dealstage": hs_stage}}
        self._hs_request("PATCH", f"crm/v3/objects/deals/{deal_id}", payload)
        self._record_audit("update_deal_stage", "deal", deal_id, {"stage": stage}, actor)
        return {"id": deal_id, "stage": stage}

    def log_interaction(
        self,
        contact_id: int,
        kind: str,
        summary: str,
        idempotency_key: str | None = None,
        actor: str = "system",
    ) -> dict:
        if idempotency_key and idempotency_key in self._idempotency_keys:
            return dict(self._idempotency_keys[idempotency_key])

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
            self._interactions[iid] = dict(interaction)
        else:
            payload = {
                "properties": {
                    "hs_timestamp": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
                    "hs_note_body": f"[{kind.upper()}] {summary}",
                },
                "associations": [{
                    "to": {"id": str(contact_id)},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}],
                }],
            }
            try:
                self._hs_request("POST", "crm/v3/objects/notes", payload)
            except Exception as e:
                log.warning("Could not persist HubSpot note: %s", e)

        self._record_audit("log_interaction", "interaction", iid, {"contact_id": contact_id, "kind": kind, "summary": summary}, actor)

        if idempotency_key:
            self._idempotency_keys[idempotency_key] = dict(interaction)

        return interaction
