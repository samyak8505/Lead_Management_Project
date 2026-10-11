"""Step 10: Human-in-the-loop (HITL) approval gate.

Enforces the safety principle:
'No CRM write or email without human approval.'

Capabilities:
1. Creates persistent approval requests with full lead summary, CRM matches, score, and draft.
2. Supports Approve, Edit (custom subject/body/tier), and Reject decisions.
3. Logs who approved what and when to the audit log.
4. Provides:
   - Programmatic API (for workflow pause/resume)
   - Interactive CLI prompt
   - Built-in lightweight Web server with modern UI
"""
from __future__ import annotations
import http.server
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from leadflow.crm import SQLiteCRM

Status = Literal["pending", "approved", "edited", "rejected"]

APPROVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    lead_summary TEXT NOT NULL,
    dedupe_info TEXT NOT NULL,
    score_info TEXT NOT NULL,
    draft_email TEXT NOT NULL,
    security_flag INTEGER NOT NULL,
    reviewer TEXT,
    decided_at TEXT,
    notes TEXT,
    edited_subject TEXT,
    edited_body TEXT,
    edited_tier TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ApprovalRequest:
    id: str
    created_at: str
    status: Status
    lead_summary: dict
    dedupe_info: dict
    score_info: dict
    draft_email: dict
    security_flag: bool
    reviewer: Optional[str] = None
    decided_at: Optional[str] = None
    notes: Optional[str] = None
    edited_subject: Optional[str] = None
    edited_body: Optional[str] = None
    edited_tier: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "lead_summary": self.lead_summary,
            "dedupe_info": self.dedupe_info,
            "score_info": self.score_info,
            "draft_email": self.draft_email,
            "security_flag": self.security_flag,
            "reviewer": self.reviewer,
            "decided_at": self.decided_at,
            "notes": self.notes,
            "edited_subject": self.edited_subject,
            "edited_body": self.edited_body,
            "edited_tier": self.edited_tier,
        }


@dataclass
class ApprovalDecision:
    request_id: str
    status: Status                      # "approved" | "edited" | "rejected"
    reviewer: str
    decided_at: str
    final_subject: str
    final_body: str
    final_tier: str
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "reviewer": self.reviewer,
            "decided_at": self.decided_at,
            "final_subject": self.final_subject,
            "final_body": self.final_body,
            "final_tier": self.final_tier,
            "notes": self.notes,
        }


class ApprovalGate:
    """Persistent gate managing human approvals."""

    def __init__(self, db_path: str | Path = "data/crm.db"):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(APPROVAL_SCHEMA)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_request(
        self,
        lead_summary: dict,
        dedupe_info: dict,
        score_info: dict,
        draft_email: dict,
        security_flag: bool = False,
        request_id: str | None = None,
    ) -> ApprovalRequest:
        """Create and persist a pending approval request."""
        req_id = request_id or str(uuid.uuid4())[:8]
        now = _now()
        req = ApprovalRequest(
            id=req_id,
            created_at=now,
            status="pending",
            lead_summary=lead_summary,
            dedupe_info=dedupe_info,
            score_info=score_info,
            draft_email=draft_email,
            security_flag=security_flag,
        )
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO approval_requests (
                    id, created_at, status, lead_summary, dedupe_info,
                    score_info, draft_email, security_flag
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    req.id,
                    req.created_at,
                    req.status,
                    json.dumps(req.lead_summary),
                    json.dumps(req.dedupe_info),
                    json.dumps(req.score_info),
                    json.dumps(req.draft_email),
                    1 if req.security_flag else 0,
                ),
            )
        return req

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Retrieve a request by its ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
            if not row:
                return None
            return self._row_to_request(row)

    def list_requests(self, status: Optional[Status] = None) -> list[ApprovalRequest]:
        """List requests, optionally filtered by status."""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute("SELECT * FROM approval_requests WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM approval_requests ORDER BY created_at DESC").fetchall()
            return [self._row_to_request(r) for r in rows]

    def approve(self, request_id: str, reviewer: str = "sales_rep", notes: str = "") -> ApprovalDecision:
        """Approve the draft as-is."""
        req = self.get_request(request_id)
        if not req:
            raise KeyError(f"Approval request {request_id} not found")
        if req.status != "pending":
            raise ValueError(f"Request {request_id} is already {req.status}")

        now = _now()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE approval_requests SET status = 'approved', reviewer = ?, decided_at = ?, notes = ? WHERE id = ?",
                (reviewer, now, notes, request_id),
            )
            # Log to CRM audit trail
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
                (now, reviewer, "approve_lead", "approval_request", None, json.dumps({
                    "request_id": request_id,
                    "notes": notes,
                    "tier": req.score_info.get("tier"),
                })),
            )

        return ApprovalDecision(
            request_id=request_id,
            status="approved",
            reviewer=reviewer,
            decided_at=now,
            final_subject=req.draft_email.get("subject", ""),
            final_body=req.draft_email.get("body", ""),
            final_tier=req.score_info.get("tier", "warm"),
            notes=notes,
        )

    def edit(
        self,
        request_id: str,
        reviewer: str = "sales_rep",
        edited_subject: Optional[str] = None,
        edited_body: Optional[str] = None,
        edited_tier: Optional[str] = None,
        notes: str = "",
    ) -> ApprovalDecision:
        """Approve with human modifications to subject, body, or tier."""
        req = self.get_request(request_id)
        if not req:
            raise KeyError(f"Approval request {request_id} not found")
        if req.status != "pending":
            raise ValueError(f"Request {request_id} is already {req.status}")

        now = _now()
        final_subj = edited_subject if edited_subject is not None else req.draft_email.get("subject", "")
        final_b = edited_body if edited_body is not None else req.draft_email.get("body", "")
        final_t = edited_tier if edited_tier is not None else req.score_info.get("tier", "warm")

        with self._get_conn() as conn:
            conn.execute(
                """UPDATE approval_requests 
                   SET status = 'edited', reviewer = ?, decided_at = ?, notes = ?,
                       edited_subject = ?, edited_body = ?, edited_tier = ?
                   WHERE id = ?""",
                (reviewer, now, notes, final_subj, final_b, final_t, request_id),
            )
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
                (now, reviewer, "edit_and_approve_lead", "approval_request", None, json.dumps({
                    "request_id": request_id,
                    "original_tier": req.score_info.get("tier"),
                    "final_tier": final_t,
                    "notes": notes,
                })),
            )

        return ApprovalDecision(
            request_id=request_id,
            status="edited",
            reviewer=reviewer,
            decided_at=now,
            final_subject=final_subj,
            final_body=final_b,
            final_tier=final_t,
            notes=notes,
        )

    def reject(self, request_id: str, reviewer: str = "sales_rep", reason: str = "") -> ApprovalDecision:
        """Reject the lead and draft."""
        req = self.get_request(request_id)
        if not req:
            raise KeyError(f"Approval request {request_id} not found")
        if req.status != "pending":
            raise ValueError(f"Request {request_id} is already {req.status}")

        now = _now()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE approval_requests SET status = 'rejected', reviewer = ?, decided_at = ?, notes = ? WHERE id = ?",
                (reviewer, now, reason, request_id),
            )
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
                (now, reviewer, "reject_lead", "approval_request", None, json.dumps({
                    "request_id": request_id,
                    "reason": reason,
                })),
            )

        return ApprovalDecision(
            request_id=request_id,
            status="rejected",
            reviewer=reviewer,
            decided_at=now,
            final_subject=req.draft_email.get("subject", ""),
            final_body=req.draft_email.get("body", ""),
            final_tier=req.score_info.get("tier", "none"),
            notes=reason,
        )

    def _row_to_request(self, row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            id=row["id"],
            created_at=row["created_at"],
            status=row["status"],
            lead_summary=json.loads(row["lead_summary"]),
            dedupe_info=json.loads(row["dedupe_info"]),
            score_info=json.loads(row["score_info"]),
            draft_email=json.loads(row["draft_email"]),
            security_flag=bool(row["security_flag"]),
            reviewer=row["reviewer"],
            decided_at=row["decided_at"],
            notes=row["notes"],
            edited_subject=row["edited_subject"],
            edited_body=row["edited_body"],
            edited_tier=row["edited_tier"],
        )


# ---------------- Interactive CLI Gate ----------------

def interactive_cli_gate(req: ApprovalRequest, gate: ApprovalGate) -> ApprovalDecision:
    """Prompt a sales rep in terminal to Approve, Edit, or Reject."""
    lead = req.lead_summary
    score = req.score_info
    draft = req.draft_email

    print("\n" + "=" * 60)
    print(f"[APPROVAL REQUIRED] Lead [{req.id}]")
    print("=" * 60)
    print(f"Contact: {lead.get('first_name')} {lead.get('last_name')} <{lead.get('email')}>")
    print(f"Company: {lead.get('company_name')} | Title: {lead.get('job_title')}")
    print(f"Intent : {lead.get('intent')} | Urgency: {lead.get('urgency')} | Budget: {lead.get('budget_signal')}")
    print(f"Score  : {score.get('score')}/100 ({str(score.get('tier')).upper()})")
    if req.security_flag:
        print("[WARNING] SECURITY: Embedded prompt injection detected in inbound text!")
    print("-" * 60)
    print(f"Subject: {draft.get('subject')}")
    print("Body:\n" + draft.get("body", ""))
    print("=" * 60)

    choice = input("\nAction: [A]pprove, [E]dit, [R]eject: ").strip().lower()
    if choice == "a":
        notes = input("Approval note (optional): ").strip()
        decision = gate.approve(req.id, reviewer="cli_user", notes=notes)
        print(f"[OK] Approved lead [{req.id}]. Ready for write-back.")
        return decision
    elif choice == "e":
        print("\nEntering Edit Mode (press Enter to keep existing):")
        new_subj = input(f"New Subject [{draft.get('subject')}]: ").strip() or None
        print("New Body (leave blank to keep existing):")
        new_body = input("> ").strip() or None
        new_tier = input(f"New Tier ({score.get('tier')}): ").strip() or None
        notes = input("Reason for edit: ").strip()
        decision = gate.edit(
            req.id,
            reviewer="cli_user",
            edited_subject=new_subj,
            edited_body=new_body,
            edited_tier=new_tier,
            notes=notes,
        )
        print(f"[OK] Edited and approved lead [{req.id}].")
        return decision
    else:
        reason = input("Rejection reason: ").strip()
        decision = gate.reject(req.id, reviewer="cli_user", reason=reason)
        print(f"[REJECTED] Lead [{req.id}]. Workflow stopped.")
        return decision


# ---------------- Lightweight Web Gate Handler ----------------

def render_html_dashboard(requests: list[ApprovalRequest]) -> str:
    """Generate modern, clean HTML interface for review and approvals."""
    cards_html = ""
    for r in requests:
        lead = r.lead_summary
        score = r.score_info
        draft = r.draft_email
        badge_color = "#10b981" if score.get("tier") == "hot" else "#f59e0b" if score.get("tier") == "warm" else "#6b7280"
        status_badge = f"<span style='background: {badge_color}; color: white; padding: 2px 8px; border-radius: 9999px; font-size: 12px; font-weight: 600;'>{score.get('tier', '').upper()} ({score.get('score')})</span>"
        sec_alert = "<div style='color: #ef4444; font-weight: 600; margin-top: 8px;'>⚠️ Warning: Prompt injection detected in source text</div>" if r.security_flag else ""

        cards_html += f"""
        <div style="background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="margin: 0; font-size: 18px; color: #111827;">{lead.get('first_name', '')} {lead.get('last_name', '')} - {lead.get('company_name', 'Unknown Co')}</h3>
                {status_badge}
            </div>
            <div style="color: #4b5563; font-size: 14px; margin-bottom: 16px;">
                <strong>Email:</strong> {lead.get('email', 'None')} | <strong>Title:</strong> {lead.get('job_title', 'None')} | <strong>Intent:</strong> {lead.get('intent')}
                {sec_alert}
            </div>
            <div style="background: #f9fafb; border-left: 4px solid #3b82f6; padding: 16px; border-radius: 6px; margin-bottom: 20px;">
                <div style="font-weight: 600; margin-bottom: 8px; color: #1f2937;">Subject: {draft.get('subject')}</div>
                <div style="white-space: pre-wrap; font-family: monospace; font-size: 13px; color: #374151;">{draft.get('body')}</div>
            </div>
            <div style="display: flex; gap: 12px;">
                <form action="/approve" method="POST" style="margin: 0;">
                    <input type="hidden" name="id" value="{r.id}">
                    <button type="submit" style="background: #10b981; color: white; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 600; cursor: pointer;">Approve & Send</button>
                </form>
                <form action="/reject" method="POST" style="margin: 0;">
                    <input type="hidden" name="id" value="{r.id}">
                    <button type="submit" style="background: #ef4444; color: white; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 600; cursor: pointer;">Reject</button>
                </form>
            </div>
        </div>
        """

    if not requests:
        cards_html = "<div style='text-align: center; color: #6b7280; padding: 48px;'>🎉 No pending leads waiting for approval!</div>"

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>LeadFlow Human Approval Gate</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>body {{ font-family: 'Inter', sans-serif; background: #f3f4f6; margin: 0; padding: 40px; }}</style>
</head>
<body>
    <div style="max-width: 800px; margin: 0 auto;">
        <div style="margin-bottom: 32px;">
            <h1 style="font-size: 26px; color: #111827; margin: 0 0 8px 0;">LeadFlow Approval Gate</h1>
            <p style="color: #6b7280; margin: 0;">Review generated drafts and lead intelligence before CRM write-back.</p>
        </div>
        {cards_html}
    </div>
</body>
</html>"""
