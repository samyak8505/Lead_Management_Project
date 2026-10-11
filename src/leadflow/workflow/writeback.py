"""Step 11: Safe write-back module.

Enforces:
1. Approval precondition: Cannot write back without an 'approved' or 'edited' decision.
2. Least privilege: LLM never has write credentials; pure deterministic code only.
3. Idempotency: Retries with the same key replay the previous result without duplicate rows.
4. Transactional integrity: Full atomic rollback on partial failure (all-or-nothing).
5. Auditability: Every action is written to the CRM audit_log.
"""
from __future__ import annotations
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from leadflow.crm import SQLiteCRM
from leadflow.workflow.approval import ApprovalDecision, ApprovalRequest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class WriteBackResult:
    success: bool
    contact_id: Optional[int] = None
    deal_id: Optional[int] = None
    interaction_id: Optional[int] = None
    email_sent: bool = False
    email_id: Optional[str] = None
    replayed: bool = False
    audit_entries: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "contact_id": self.contact_id,
            "deal_id": self.deal_id,
            "interaction_id": self.interaction_id,
            "email_sent": self.email_sent,
            "email_id": self.email_id,
            "replayed": self.replayed,
            "audit_entries": self.audit_entries,
            "error": self.error,
        }


def sandbox_email_sender(to_email: str, subject: str, body: str) -> str:
    """Mock sandbox sender simulating successful email transmission."""
    msg_id = f"mock-msg-{uuid.uuid4().hex[:12]}"
    return msg_id


def execute_write_back(
    request: ApprovalRequest,
    decision: ApprovalDecision,
    crm: SQLiteCRM,
    idempotency_key: Optional[str] = None,
    send_email_fn: Optional[Callable[[str, str, str], str]] = sandbox_email_sender,
) -> WriteBackResult:
    """Execute atomic, idempotent write-back to CRM following human approval."""
    # 1. Enforce Preconditions (Zero Unapproved Writes)
    if decision.status not in ("approved", "edited"):
        raise PermissionError(
            f"Write-back forbidden for decision status '{decision.status}'. Human approval is required."
        )

    actor = decision.reviewer or "sales_rep"
    key = idempotency_key or f"wb-{request.id}"

    # 2. Check for Previous Replay (Idempotency Key Check)
    with crm.conn:
        row = crm.conn.execute(
            "SELECT result_id, entity FROM idempotency_keys WHERE key = ?", (key,)
        ).fetchone()
        if row:
            cid = row["result_id"]
            deal = crm._one("SELECT id FROM deals WHERE contact_id = ? ORDER BY id DESC LIMIT 1", (cid,))
            interaction = crm._one("SELECT id FROM interactions WHERE contact_id = ? ORDER BY id DESC LIMIT 1", (cid,))
            return WriteBackResult(
                success=True,
                contact_id=cid,
                deal_id=deal["id"] if deal else None,
                interaction_id=interaction["id"] if interaction else None,
                email_sent=True,
                email_id=f"replayed-{key}",
                replayed=True,
                audit_entries=["write_back_replayed"],
            )

    lead = request.lead_summary
    dedupe_info = request.dedupe_info
    tier = decision.final_tier or "warm"
    subject = decision.final_subject
    body = decision.final_body

    contact_id: int | None = None
    deal_id: int | None = None
    interaction_id: int | None = None
    email_id: str | None = None
    audit_entries: list[str] = []

    # 3. Single Atomic Transaction Block
    try:
        with crm.conn:
            now = _now()
            is_existing = dedupe_info.get("decision") == "existing" and dedupe_info.get("match_ids")

            # A) Contact Creation or Update
            if is_existing:
                contact_id = dedupe_info["match_ids"][0]
                updates = []
                params = []
                if lead.get("job_title"):
                    updates.append("title = ?")
                    params.append(lead["job_title"])
                if lead.get("company_name"):
                    updates.append("company_name = ?")
                    params.append(lead["company_name"])
                if updates:
                    updates.append("updated_at = ?")
                    params.append(now)
                    params.append(contact_id)
                    crm.conn.execute(f"UPDATE contacts SET {', '.join(updates)} WHERE id = ?", tuple(params))
                    audit_entries.append(f"updated_contact_{contact_id}")
            else:
                cur_c = crm.conn.execute(
                    """INSERT INTO contacts (
                        first_name, last_name, email, company_name, title, source, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        lead.get("first_name"),
                        lead.get("last_name"),
                        lead.get("email"),
                        lead.get("company_name"),
                        lead.get("job_title"),
                        "leadflow_inbound",
                        now,
                        now,
                    ),
                )
                contact_id = cur_c.lastrowid
                audit_entries.append(f"created_contact_{contact_id}")

            # B) Deal Creation for High-Intent Prospects (hot or warm)
            if tier in ("hot", "warm"):
                amount = 25000.0 if tier == "hot" else 10000.0
                intent_name = str(lead.get("intent", "Inquiry")).replace("_", " ").title()
                company_label = lead.get("company_name") or "New Prospect"
                deal_name = f"{company_label} - {intent_name}"

                cur_d = crm.conn.execute(
                    """INSERT INTO deals (
                        contact_id, name, amount, stage, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?)""",
                    (contact_id, deal_name, amount, "new", now, now),
                )
                deal_id = cur_d.lastrowid
                audit_entries.append(f"created_deal_{deal_id}")

            # C) Interaction Logging
            interaction_summary = f"Outbound Email ({decision.status.title()} by {actor}): {subject}"
            cur_i = crm.conn.execute(
                """INSERT INTO interactions (
                    contact_id, kind, summary, created_at
                ) VALUES (?,?,?,?)""",
                (contact_id, "email", interaction_summary, now),
            )
            interaction_id = cur_i.lastrowid
            audit_entries.append(f"logged_interaction_{interaction_id}")

            # D) Email Dispatch (Sandbox or Live Provider)
            # If sending throws an exception, the entire transaction rolls back!
            to_addr = lead.get("email") or "sandbox@leadflow.local"
            if send_email_fn:
                email_id = send_email_fn(to_addr, subject, body)
                audit_entries.append(f"dispatched_email_{email_id}")

            # E) Store Idempotency Keys (for writeback and individual entities)
            crm.conn.execute(
                "INSERT INTO idempotency_keys (key, entity, result_id, created_at) VALUES (?,?,?,?)",
                (key, "writeback", contact_id, now),
            )
            crm.conn.execute(
                "INSERT INTO idempotency_keys (key, entity, result_id, created_at) VALUES (?,?,?,?)",
                (f"c-{key}", "contact", contact_id, now),
            )
            if deal_id:
                crm.conn.execute(
                    "INSERT INTO idempotency_keys (key, entity, result_id, created_at) VALUES (?,?,?,?)",
                    (f"d-{key}", "deal", deal_id, now),
                )
            crm.conn.execute(
                "INSERT INTO idempotency_keys (key, entity, result_id, created_at) VALUES (?,?,?,?)",
                (f"i-{key}", "interaction", interaction_id, now),
            )

            # F) Comprehensive Audit Log Entry
            crm.conn.execute(
                "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
                (
                    now,
                    actor,
                    "execute_write_back",
                    "contact",
                    contact_id,
                    json.dumps({
                        "request_id": request.id,
                        "deal_id": deal_id,
                        "interaction_id": interaction_id,
                        "email_id": email_id,
                        "tier": tier,
                        "idempotency_key": key,
                    }),
                ),
            )
            audit_entries.append("recorded_audit_log")

    except Exception as e:
        # Upon exception, SQLite automatically rolls back the entire transaction!
        # In a separate transaction, log the failure to audit_log
        try:
            with crm.conn:
                crm.conn.execute(
                    "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
                    (
                        _now(),
                        actor,
                        "write_back_failed",
                        "approval_request",
                        None,
                        json.dumps({"request_id": request.id, "error": str(e)}),
                    ),
                )
        except Exception:
            pass

        return WriteBackResult(
            success=False,
            contact_id=None,
            deal_id=None,
            interaction_id=None,
            email_sent=False,
            error=str(e),
            audit_entries=["rolled_back_transaction"],
        )

    return WriteBackResult(
        success=True,
        contact_id=contact_id,
        deal_id=deal_id,
        interaction_id=interaction_id,
        email_sent=bool(email_id),
        email_id=email_id,
        replayed=False,
        audit_entries=audit_entries,
    )
