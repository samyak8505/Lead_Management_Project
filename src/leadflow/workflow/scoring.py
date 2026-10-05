"""Step 7: Enrichment and scoring step (parallel execution).

Subtasks executed concurrently:
1. Company profile / size lookup from CRM or domain heuristics.
2. CRM interaction & deal history for matched contacts.
3. Intent, urgency, budget, and authority signal evaluation.

Combines into an explainable 0-100 score, tier (hot/warm/cold/none),
transparent point breakdown, and a concise rationale.
"""
from __future__ import annotations
import concurrent.futures
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

from leadflow import llm as _llm
from leadflow.crm import SQLiteCRM
from leadflow.workflow.dedupe import DedupeResult

Tier = Literal["hot", "warm", "cold", "none"]

DM_RE = re.compile(
    r"\b(c[a-z]o|ceo|cto|cfo|coo|cmo|cio|cpo|chief|founder|co-founder|owner|"
    r"vp|vice president|director|head of|managing partner|managing director|general manager)\b",
    re.I,
)


class LLMScoringRationale(BaseModel):
    rationale: str = Field(description="A 1-2 sentence concise explanation of why this lead received this score and tier")


@dataclass
class ScoreResult:
    score: int
    tier: Tier
    reasons: list[str] = field(default_factory=list)
    rationale: str = ""
    enrichment: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "tier": self.tier,
            "reasons": self.reasons,
            "rationale": self.rationale,
            "enrichment": self.enrichment,
        }


# ---------------- Parallel Subtasks ----------------

def _lookup_company(company_name: str | None, crm: SQLiteCRM | None) -> dict:
    """Enrichment subtask 1: Company size and industry lookup."""
    info = {"employee_count": None, "industry": None, "domain": None, "source": "unknown"}
    if not company_name:
        return info

    clean = company_name.strip().lower()
    if crm and hasattr(crm, "path"):
        try:
            import sqlite3
            conn = sqlite3.connect(crm.path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM companies WHERE lower(name) LIKE ? OR ? LIKE '%' || lower(name) || '%' LIMIT 1",
                (f"%{clean}%", clean)
            ).fetchone()
            if row:
                info["employee_count"] = row["employee_count"]
                info["industry"] = row["industry"]
                info["domain"] = row["domain"]
                info["source"] = "crm_database"
            conn.close()
        except Exception:
            pass

    if info["source"] == "unknown":
        # Heuristic estimation for common business indicators
        info["source"] = "heuristic"
        if any(w in clean for w in ("hotel", "group", "hospital", "university", "enterprise", "holdings")):
            info["employee_count"] = 250
        elif any(w in clean for w in ("consulting", "agency", "tech", "software", "solutions")):
            info["employee_count"] = 50
        else:
            info["employee_count"] = 15
    return info


def _lookup_history(dedupe_res: DedupeResult | None, crm: SQLiteCRM | None) -> dict:
    """Enrichment subtask 2: Past interactions and deals for matched contacts."""
    history = {"past_interactions": 0, "past_deals": 0, "won_deals": 0, "is_existing_customer": False}
    if not dedupe_res or not dedupe_res.match_ids or not crm or not hasattr(crm, "path"):
        return history

    try:
        import sqlite3
        conn = sqlite3.connect(crm.path)
        conn.row_factory = sqlite3.Row
        total_interactions = 0
        total_deals = 0
        won_deals = 0

        for cid in dedupe_res.match_ids:
            cur_i = conn.execute("SELECT COUNT(*) c FROM interactions WHERE contact_id = ?", (cid,)).fetchone()
            total_interactions += cur_i["c"] if cur_i else 0

            deals = conn.execute("SELECT stage FROM deals WHERE contact_id = ?", (cid,)).fetchall()
            total_deals += len(deals)
            won_deals += sum(1 for d in deals if d["stage"] == "won")

        conn.close()
        history["past_interactions"] = total_interactions
        history["past_deals"] = total_deals
        history["won_deals"] = won_deals
        history["is_existing_customer"] = won_deals > 0 or total_interactions > 2
    except Exception:
        pass

    return history


def _evaluate_signals(extraction: dict, raw: str) -> dict:
    """Enrichment subtask 3: Authority, ask, urgency, and budget signal analysis."""
    title = extraction.get("job_title") or ""
    t_lower = title.strip().lower()

    # Decision maker check (excluding plain non-executive managers)
    is_dm = False
    if t_lower:
        is_plain_mgr = ("manager" in t_lower or "lead" in t_lower) and not any(
            x in t_lower for x in ("head of", "director", "vp", "vice president", "general manager")
        )
        if not is_plain_mgr:
            is_dm = bool(DM_RE.search(t_lower))

    intent = extraction.get("intent") or "unclear"
    urgency = extraction.get("urgency") or "low"
    budget = extraction.get("budget_signal") or "none"

    # Concrete ask check
    has_ask = intent in ("demo_request", "pricing_inquiry")
    if not has_ask and raw:
        lines = [l for l in raw.splitlines() if not l.startswith(("From:", "Email:", "To:"))]
        body = re.sub(r"[\w.+-]+@[\w.-]+", "", " ".join(lines)).lower()
        has_ask = bool(re.search(r"\b(please call me|give me a call|schedule a call|schedule a demo|request a demo|send pricing|quote)\b", body))

    # Early research / informational request check
    is_research = False
    if raw and intent not in ("demo_request", "pricing_inquiry"):
        lines = [l for l in raw.splitlines() if not l.startswith(("From:", "Email:", "To:"))]
        body = re.sub(r"[\w.+-]+@[\w.-]+", "", " ".join(lines)).lower()
        is_research = bool(re.search(r"\b(documentation|whitepaper|case studies|mailing list|newsletter|just beginning to research|exploring tools)\b", body))

    return {
        "is_decision_maker": is_dm,
        "has_concrete_ask": has_ask,
        "is_high_urgency": urgency == "high",
        "is_explicit_budget": budget == "explicit",
        "is_implied_budget": budget == "implied",
        "is_early_research": is_research,
    }


# ---------------- Score Aggregation & Tiering ----------------

def score_lead(
    raw: str,
    extraction: dict,
    dedupe_res: DedupeResult | None = None,
    crm: SQLiteCRM | None = None,
    llm_fn=None,
) -> ScoreResult:
    """Run parallel enrichment and compute explainable score, tier, and rationale."""
    # Run subtasks concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f_company = executor.submit(_lookup_company, extraction.get("company_name"), crm)
        f_history = executor.submit(_lookup_history, dedupe_res, crm)
        f_signals = executor.submit(_evaluate_signals, extraction, raw)

        company_info = f_company.result()
        history_info = f_history.result()
        signals = f_signals.result()

    intent = extraction.get("intent") or "unclear"
    reasons: list[str] = []
    score = 0

    # Non-sales categories receive score=0 and tier='none'
    if intent in ("support_request", "spam", "vendor_pitch", "unclear"):
        reasons.append(f"non_sales_intent:{intent}")
        return ScoreResult(
            score=0,
            tier="none",
            reasons=reasons,
            rationale=f"Classified as non-sales lead due to intent '{intent}'.",
            enrichment={"company": company_info, "history": history_info, "signals": signals},
        )

    # Early research / documentation requests route to nurture
    if signals["is_early_research"]:
        score = 15
        reasons.append("early_stage_research_inquiry")
        tier: Tier = "cold"
    else:
        # Transparent 4-criteria rubric
        c1 = signals["is_decision_maker"]
        c2 = signals["has_concrete_ask"]
        c3 = signals["is_high_urgency"]
        c4 = signals["is_explicit_budget"]

        if c1:
            score += 25
            reasons.append("decision_maker_authority (+25)")
        if c2:
            score += 25
            reasons.append("concrete_ask_for_demo_pricing_or_meeting (+25)")
        if c3:
            score += 25
            reasons.append("high_urgency_timeline_within_30_days (+25)")
        if c4:
            score += 25
            reasons.append("explicit_budget_stated (+25)")

        if signals["is_implied_budget"] and not c4:
            score += 10
            reasons.append("implied_budget_from_unit_or_seat_count (+10)")

        if intent == "partnership":
            score += 20
            reasons.append("partnership_inquiry (+20)")

        # Enrichment bonuses
        emp_count = company_info.get("employee_count") or 0
        if emp_count >= 100:
            score += 5
            reasons.append(f"mid_market_or_enterprise_company_size_{emp_count} (+5)")

        if history_info.get("is_existing_customer"):
            score += 5
            reasons.append("prior_crm_relationship_or_won_deal (+5)")

        score = min(score, 100)
        criteria_count = sum([c1, c2, c3, c4])

        if intent == "partnership":
            tier = "warm"
        elif criteria_count >= 3:
            tier = "hot"
        elif criteria_count in (1, 2):
            tier = "warm"
        else:
            tier = "cold"

    # Generate concise rationale
    rationale = f"Lead assigned {score}/100 ({tier.upper()}). " + "; ".join(reasons[:2]) + "."
    if llm_fn:
        try:
            prompt = (
                f"Lead Details:\n"
                f"- Name: {extraction.get('first_name')} {extraction.get('last_name')}\n"
                f"- Title: {extraction.get('job_title')}\n"
                f"- Company: {extraction.get('company_name')}\n"
                f"- Intent: {intent}, Urgency: {extraction.get('urgency')}, Budget: {extraction.get('budget_signal')}\n"
                f"- Score: {score}, Tier: {tier}\n"
                f"- Reasons: {', '.join(reasons)}\n\n"
                f"Write a 1-sentence sales rationale explaining this score and tier."
            )
            res = llm_fn(
                prompt,
                system="You are a CRM sales intelligence assistant. Provide a brief 1-sentence explanation.",
                max_tokens=100,
                schema=LLMScoringRationale,
            )
            parsed = LLMScoringRationale.model_validate(json.loads(res["text"]))
            rationale = parsed.rationale
        except Exception:
            pass

    return ScoreResult(
        score=score,
        tier=tier,
        reasons=reasons,
        rationale=rationale,
        enrichment={"company": company_info, "history": history_info, "signals": signals},
    )
