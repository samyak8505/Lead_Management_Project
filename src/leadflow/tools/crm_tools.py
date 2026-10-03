"""Tool handlers: thin, deterministic functions. Signature: (crm, actor, validated_input) -> dict.
No LLM here, and no deletes anywhere (least privilege)."""
from __future__ import annotations
from leadflow.crm import CRMClient
from . import schemas as s

_SLIM = ("id", "first_name", "last_name", "email", "phone", "title", "company_name", "source")


def _slim(c: dict) -> dict:
    return {k: c.get(k) for k in _SLIM}


# ---------- reads ----------
def search_contact(crm: CRMClient, actor: str, inp: s.SearchContactInput) -> dict:
    if inp.email:
        rows = crm.find_by_email(inp.email)[: inp.limit]
        mode = "email"
    else:
        rows = crm.search_contacts(name=inp.name, company=inp.company, limit=inp.limit)
        mode = "name_company"
    return {"match_mode": mode, "count": len(rows), "matches": [_slim(r) for r in rows]}


def get_contact_details(crm: CRMClient, actor: str, inp: s.GetContactDetailsInput) -> dict:
    c = crm.get_contact(inp.contact_id)
    if not c:
        raise KeyError(f"contact {inp.contact_id} not found")
    return {
        "contact": _slim(c),
        "deals": [{k: d[k] for k in ("id", "name", "amount", "stage")} for d in crm.list_deals(inp.contact_id)],
        "recent_interactions": [{k: i[k] for k in ("kind", "summary", "created_at")}
                                for i in crm.list_interactions(inp.contact_id, limit=5)],
    }


# ---------- writes ----------
def create_contact(crm: CRMClient, actor: str, inp: s.CreateContactInput) -> dict:
    fields = inp.model_dump(exclude_none=True)
    key = fields.pop("idempotency_key")
    c = crm.create_contact(fields, idempotency_key=key, actor=actor, reject_duplicate_email=True)
    return {"contact": _slim(c), "replayed": bool(c.get("_replayed"))}


def update_contact(crm: CRMClient, actor: str, inp: s.UpdateContactInput) -> dict:
    fields = inp.model_dump(exclude_none=True)
    cid = fields.pop("contact_id")
    return {"contact": _slim(crm.update_contact(cid, fields, actor=actor))}


def create_deal(crm: CRMClient, actor: str, inp: s.CreateDealInput) -> dict:
    d = crm.create_deal(inp.contact_id, inp.name, inp.amount, inp.stage,
                        idempotency_key=inp.idempotency_key, actor=actor)
    return {"deal": {k: d[k] for k in ("id", "contact_id", "name", "amount", "stage")},
            "replayed": bool(d.get("_replayed"))}


def update_deal_stage(crm: CRMClient, actor: str, inp: s.UpdateDealStageInput) -> dict:
    d = crm.update_deal_stage(inp.deal_id, inp.stage, actor=actor)
    return {"deal": {k: d[k] for k in ("id", "contact_id", "name", "amount", "stage")}}


def log_interaction(crm: CRMClient, actor: str, inp: s.LogInteractionInput) -> dict:
    i = crm.log_interaction(inp.contact_id, inp.kind, inp.summary,
                            idempotency_key=inp.idempotency_key, actor=actor)
    return {"interaction": {k: i[k] for k in ("id", "contact_id", "kind", "summary")},
            "replayed": bool(i.get("_replayed"))}
