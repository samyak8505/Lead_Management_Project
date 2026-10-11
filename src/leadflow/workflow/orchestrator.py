"""Step 12: Workflow Orchestration and State Engine.

Wires all pipeline steps end-to-end:
1. Extract (Structured LLM extraction)
2. Dedupe (Deterministic, fuzzy, and LLM candidate matching)
3. Score (Parallel enrichment + transparent scoring)
4. Route (Sales, nurture, support, discard)
5. Draft (Generator-critic bounded loop)
6. Approval Gate (Human review checkpoint with pause & resume)
7. Safe Write-Back (Atomic transactional update to CRM)

Features:
- Persistent SQLite checkpointing after every step so crashed runs can resume.
- Step-count limits (max 15 steps) and cost limits (max $0.10).
- Support for CLI execution, auto-approval, or paused human review.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional

from leadflow.crm import SQLiteCRM
from leadflow.observability.tracer import get_tracer
from leadflow.workflow.approval import ApprovalDecision, ApprovalGate, ApprovalRequest, interactive_cli_gate
from leadflow.workflow.dedupe import DedupeResult, dedupe
from leadflow.workflow.draft import DraftResult, draft_email
from leadflow.workflow.extract import ExtractionResult, extract
from leadflow.workflow.route import RouteResult, route_lead
from leadflow.workflow.scoring import ScoreResult, score_lead
from leadflow.workflow.writeback import WriteBackResult, execute_write_back

DEFAULT_DB = "data/crm.db"
MAX_STEPS = 15
MAX_COST_DOLLARS = 0.10

# Gemini 2.5 Flash token cost estimates per 1,000 tokens
INPUT_TOKEN_COST_PER_K = 0.000075
OUTPUT_TOKEN_COST_PER_K = 0.00030

CHECKPOINT_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    current_step TEXT NOT NULL,
    step_count INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    cost REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CostLimitExceededError(RuntimeError):
    pass


class StepLimitExceededError(RuntimeError):
    pass


@dataclass
class WorkflowState:
    run_id: str
    channel: str
    raw_lead: str
    status: str = "running"              # running | waiting_for_approval | completed | discarded | routed_to_support | failed
    current_step: str = "intake"
    step_count: int = 0
    extraction: Optional[dict] = None
    injection_detected: bool = False
    dedupe: Optional[dict] = None
    scoring: Optional[dict] = None
    route: Optional[dict] = None
    draft: Optional[dict] = None
    approval_request_id: Optional[str] = None
    approval_decision: Optional[dict] = None
    write_back: Optional[dict] = None
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    estimated_cost: float = 0.0
    error: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> WorkflowState:
        return cls(**d)

    def record_usage(self, usage: dict) -> None:
        calls = usage.get("llm_calls", 0)
        inp = usage.get("input_tokens", 0)
        outp = usage.get("output_tokens", 0)
        self.llm_calls += calls
        self.input_tokens += inp
        self.output_tokens += outp
        added_cost = (inp / 1000.0 * INPUT_TOKEN_COST_PER_K) + (outp / 1000.0 * OUTPUT_TOKEN_COST_PER_K)
        self.estimated_cost = round(self.estimated_cost + added_cost, 6)

        if self.estimated_cost > MAX_COST_DOLLARS:
            raise CostLimitExceededError(
                f"Run exceeded max cost limit: ${self.estimated_cost:.4f} > ${MAX_COST_DOLLARS:.2f}"
            )


# ---------------- State Checkpointing ----------------

def _init_checkpoint_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(CHECKPOINT_SCHEMA)


def save_checkpoint(state: WorkflowState, db_path: str = DEFAULT_DB) -> None:
    """Save pipeline checkpoint to SQLite database."""
    _init_checkpoint_table(db_path)
    state.updated_at = _now()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO workflow_runs (
                run_id, status, current_step, step_count, state_json, cost, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = excluded.status,
                current_step = excluded.current_step,
                step_count = excluded.step_count,
                state_json = excluded.state_json,
                cost = excluded.cost,
                updated_at = excluded.updated_at""",
            (
                state.run_id,
                state.status,
                state.current_step,
                state.step_count,
                json.dumps(state.to_dict(), default=str),
                state.estimated_cost,
                state.created_at,
                state.updated_at,
            ),
        )


def load_checkpoint(run_id: str, db_path: str = DEFAULT_DB) -> WorkflowState | None:
    """Load workflow run state from SQLite database."""
    _init_checkpoint_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT state_json FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return WorkflowState.from_dict(json.loads(row["state_json"]))


# ---------------- Orchestrator Pipeline ----------------

def run_lead_pipeline(
    raw: str,
    channel: str = "email",
    crm: Optional[SQLiteCRM] = None,
    gate: Optional[ApprovalGate] = None,
    auto_approve: bool = False,
    interactive: bool = False,
    reviewer: str = "sales_rep",
    db_path: str = DEFAULT_DB,
    run_id: Optional[str] = None,
    resume_from_state: Optional[WorkflowState] = None,
) -> WorkflowState:
    """Run lead pipeline end-to-end with persistent checkpoints after every step."""
    crm = crm or SQLiteCRM(db_path)
    gate = gate or ApprovalGate(db_path)
    tracer = get_tracer(db_path)

    # Initialize or resume state
    if resume_from_state:
        state = resume_from_state
    else:
        state = WorkflowState(
            run_id=run_id or f"run-{uuid.uuid4().hex[:8]}",
            channel=channel,
            raw_lead=raw,
            current_step="intake",
        )
        save_checkpoint(state, db_path)

    tracer.start_trace(
        run_id=state.run_id,
        name="leadflow-pipeline",
        tags={"channel": state.channel},
        metadata={"run_id": state.run_id, "step": state.current_step},
    )

    def step_guard(step_name: str):
        state.step_count += 1
        state.current_step = step_name
        if state.step_count > MAX_STEPS:
            raise StepLimitExceededError(f"Run exceeded maximum step limit ({MAX_STEPS})")

    try:
        # Step 1: Extraction
        if not state.extraction:
            step_guard("extract")
            with tracer.span("extract", metadata={"channel": state.channel}) as sp:
                ext_res = extract(state.raw_lead, state.channel)
                state.extraction = ext_res.data
                state.injection_detected = ext_res.injection_detected
                state.record_usage(ext_res.usage)
                sp.input_tokens = ext_res.usage.get("input_tokens", 0)
                sp.output_tokens = ext_res.usage.get("output_tokens", 0)
                sp.metadata["intent"] = ext_res.data.get("intent")
                save_checkpoint(state, db_path)

        # Step 2: Deduplication
        if not state.dedupe:
            step_guard("dedupe")
            with tracer.span("dedupe") as sp:
                dedupe_res = dedupe(state.extraction, crm)
                state.dedupe = dedupe_res.to_dict()
                sp.metadata["decision"] = dedupe_res.decision
                sp.metadata["confidence"] = dedupe_res.confidence
                save_checkpoint(state, db_path)
        else:
            dedupe_res = DedupeResult(
                decision=state.dedupe["decision"],
                match_ids=state.dedupe["match_ids"],
                confidence=state.dedupe["confidence"],
                reasons=state.dedupe.get("reasons", []),
            )

        # Step 3: Scoring & Enrichment
        if not state.scoring:
            step_guard("score")
            with tracer.span("score") as sp:
                score_res = score_lead(state.raw_lead, state.extraction, dedupe_res=dedupe_res, crm=crm)
                state.scoring = score_res.to_dict()
                sp.metadata["score"] = score_res.score
                sp.metadata["tier"] = score_res.tier
                save_checkpoint(state, db_path)
        else:
            score_res = ScoreResult(
                score=state.scoring["score"],
                tier=state.scoring["tier"],
                reasons=state.scoring["reasons"],
                rationale=state.scoring.get("rationale", ""),
                enrichment=state.scoring.get("enrichment", {}),
            )

        # Step 4: Routing
        if not state.route:
            step_guard("route")
            with tracer.span("route") as sp:
                route_res = route_lead(
                    state.raw_lead,
                    state.extraction,
                    score_res,
                    injection_detected=state.injection_detected,
                )
                state.route = route_res.to_dict()
                sp.metadata["route"] = route_res.route
                save_checkpoint(state, db_path)

            if route_res.route == "discard":
                state.status = "discarded"
                save_checkpoint(state, db_path)
                tracer.end_trace(status="discarded")
                return state

            if route_res.route == "support":
                state.status = "routed_to_support"
                save_checkpoint(state, db_path)
                tracer.end_trace(status="routed_to_support")
                return state

        # Step 5: Email Drafting with Critic Loop (for sales/nurture)
        if not state.draft:
            step_guard("draft")
            with tracer.span("draft") as sp:
                draft_res = draft_email(
                    state.raw_lead,
                    state.extraction,
                    dedupe_res=dedupe_res,
                    score_res=score_res,
                )
                state.draft = draft_res.to_dict()
                state.record_usage(draft_res.usage)
                sp.input_tokens = draft_res.usage.get("input_tokens", 0)
                sp.output_tokens = draft_res.usage.get("output_tokens", 0)
                sp.metadata["critic_score"] = draft_res.critic_score
                sp.metadata["loops"] = draft_res.loops
                save_checkpoint(state, db_path)

        # Step 6: Human Approval Gate
        if not state.approval_decision:
            step_guard("approval_gate")
            with tracer.span("approval_gate") as sp:
                if not state.approval_request_id:
                    req = gate.create_request(
                        lead_summary=state.extraction,
                        dedupe_info=state.dedupe,
                        score_info=state.scoring,
                        draft_email=state.draft,
                        security_flag=state.injection_detected,
                        request_id=f"gate-{state.run_id}",
                    )
                    state.approval_request_id = req.id
                    save_checkpoint(state, db_path)
                else:
                    req = gate.get_request(state.approval_request_id)
                    assert req is not None

                # Check if approval decision is provided or requested
                if auto_approve:
                    decision = gate.approve(req.id, reviewer=reviewer, notes="Auto-approved by orchestrator")
                    state.approval_decision = decision.to_dict()
                    sp.metadata["decision"] = "approved"
                    save_checkpoint(state, db_path)
                elif interactive:
                    decision = interactive_cli_gate(req, gate)
                    state.approval_decision = decision.to_dict()
                    sp.metadata["decision"] = decision.status
                    save_checkpoint(state, db_path)
                else:
                    # Pause execution and wait for human review via web UI or CLI
                    state.status = "waiting_for_approval"
                    sp.metadata["decision"] = "paused"
                    save_checkpoint(state, db_path)

            if state.status == "waiting_for_approval":
                tracer.end_trace(status="waiting_for_approval")
                return state

        # Step 7: Safe Write-Back (if approved or edited)
        if not state.write_back:
            step_guard("write_back")
            req = gate.get_request(state.approval_request_id)
            assert req is not None
            decision = ApprovalDecision(**state.approval_decision)

            if decision.status == "rejected":
                state.status = "rejected"
                save_checkpoint(state, db_path)
                tracer.end_trace(status="rejected")
                return state

            with tracer.span("write_back") as sp:
                wb_res = execute_write_back(
                    request=req,
                    decision=decision,
                    crm=crm,
                    idempotency_key=f"wb-{state.run_id}",
                )
                state.write_back = wb_res.to_dict()
                state.status = "completed" if wb_res.success else "failed"
                sp.metadata["contact_id"] = wb_res.contact_id
                sp.metadata["deal_id"] = wb_res.deal_id
                save_checkpoint(state, db_path)

        tracer.end_trace(status=state.status)

    except Exception as e:
        state.status = "failed"
        state.error = str(e)
        save_checkpoint(state, db_path)
        tracer.end_trace(status="failed", error=str(e))
        raise

    return state


def resume_pipeline(
    run_id: str,
    db_path: str = DEFAULT_DB,
    crm: Optional[SQLiteCRM] = None,
    gate: Optional[ApprovalGate] = None,
) -> WorkflowState:
    """Resume a paused or checkpointed workflow run."""
    state = load_checkpoint(run_id, db_path)
    if not state:
        raise KeyError(f"Run ID '{run_id}' not found in checkpoints.")

    # Check if waiting for approval and decision was submitted externally
    gate = gate or ApprovalGate(db_path)
    if state.approval_request_id and not state.approval_decision:
        req = gate.get_request(state.approval_request_id)
        if req and req.status != "pending":
            state.approval_decision = ApprovalDecision(
                request_id=req.id,
                status=req.status,
                reviewer=req.reviewer or "reviewer",
                decided_at=req.decided_at or _now(),
                final_subject=req.edited_subject or req.draft_email.get("subject", ""),
                final_body=req.edited_body or req.draft_email.get("body", ""),
                final_tier=req.edited_tier or req.score_info.get("tier", "warm"),
                notes=req.notes or "",
            ).to_dict()

    return run_lead_pipeline(
        raw=state.raw_lead,
        channel=state.channel,
        crm=crm,
        gate=gate,
        db_path=db_path,
        resume_from_state=state,
    )


# ---------------- CLI Entry Point ----------------

def main():
    ap = argparse.ArgumentParser(description="Run LeadFlow end-to-end pipeline on an inbound lead.")
    ap.add_argument("--lead", help="Raw lead text")
    ap.add_argument("--file", help="Path to text file containing raw lead")
    ap.add_argument("--channel", default="email", choices=["email", "web_form"])
    ap.add_argument("--auto-approve", action="store_true", help="Auto-approve draft without interactive prompt")
    ap.add_argument("--interactive", action="store_true", help="Interactive terminal prompt for approval gate")
    ap.add_argument("--resume", help="Resume an existing run_id")
    ap.add_argument("--db", default=DEFAULT_DB, help="Path to SQLite database")
    args = ap.parse_args()

    if args.resume:
        print(f"Resuming run [{args.resume}]...")
        state = resume_pipeline(args.resume, db_path=args.db)
    else:
        text = args.lead
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        if not text:
            ap.error("Must provide either --lead or --file or --resume")

        print("[START] Starting LeadFlow Orchestration...")
        state = run_lead_pipeline(
            raw=text,
            channel=args.channel,
            auto_approve=args.auto_approve,
            interactive=args.interactive or not args.auto_approve,
            db_path=args.db,
        )

    print("\n" + "=" * 60)
    print(f"[STATUS] RUN [{state.run_id}] -> {state.status.upper()}")
    print("=" * 60)
    print(f"Current Step : {state.current_step}")
    print(f"Steps Run    : {state.step_count}")
    print(f"LLM Calls    : {state.llm_calls} (In: {state.input_tokens}, Out: {state.output_tokens})")
    print(f"Cost Estimate: ${state.estimated_cost:.5f}")
    if state.extraction:
        print(f"Contact Name : {state.extraction.get('first_name')} {state.extraction.get('last_name')}")
        print(f"Company      : {state.extraction.get('company_name')}")
    if state.scoring:
        print(f"Score / Tier : {state.scoring.get('score')} ({state.scoring.get('tier')})")
    if state.route:
        print(f"Route        : {state.route.get('route')}")
    if state.write_back:
        print(f"CRM Contact  : ID={state.write_back.get('contact_id')}")
        print(f"CRM Deal     : ID={state.write_back.get('deal_id')}")
        print(f"Interaction  : ID={state.write_back.get('interaction_id')}")
        print(f"Email Sent   : {state.write_back.get('email_sent')} (Msg: {state.write_back.get('email_id')})")
    print("=" * 60)


if __name__ == "__main__":
    main()
