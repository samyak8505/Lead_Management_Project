"""Step 8: Routing step.

Routes leads to:
- 'sales' (hot or warm tier leads)
- 'nurture' (cold tier leads, early research, low-urgency general questions)
- 'support' (support requests, bug reports, customer billing)
- 'discard' (spam, unsolicited vendor pitches, gibberish, pure injection attacks)

Combines fast deterministic rule routing with an LLM classifier fallback for fuzzy cases.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from leadflow import llm as _llm
from leadflow.workflow.scoring import ScoreResult

Route = Literal["sales", "nurture", "support", "discard"]


class LLMRouteClassification(BaseModel):
    route: Route = Field(description="Target destination: sales, nurture, support, or discard")
    confidence: float = Field(description="Confidence between 0.0 and 1.0")
    reason: str = Field(description="Reason for routing choice")


@dataclass
class RouteResult:
    route: Route
    confidence: float = 1.0
    reason: str = ""
    security_flag: bool = False

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "security_flag": self.security_flag,
        }


def route_lead(
    raw: str,
    extraction: dict,
    score_res: ScoreResult,
    injection_detected: bool = False,
    llm_fn=None,
) -> RouteResult:
    """Route lead based on extracted intent, lead tier, and security signals."""
    intent = extraction.get("intent") or "unclear"
    tier = score_res.tier

    # 1. Direct Intent Routing Rules
    if intent == "support_request":
        return RouteResult(
            route="support",
            confidence=1.0,
            reason="Customer support or post-sales inquiry routed to support queue.",
            security_flag=injection_detected,
        )

    if intent in ("spam", "vendor_pitch"):
        return RouteResult(
            route="discard",
            confidence=1.0,
            reason=f"Unwanted inbound traffic ({intent}) discarded.",
            security_flag=injection_detected,
        )

    if intent == "unclear":
        # Pure injection attacks with no genuine request
        return RouteResult(
            route="discard",
            confidence=0.95,
            reason="Inbound has no actionable sales or product request.",
            security_flag=injection_detected,
        )

    # 2. Tier-Based Routing Rules
    if tier in ("hot", "warm"):
        return RouteResult(
            route="sales",
            confidence=1.0 if not injection_detected else 0.9,
            reason=f"High-value {tier} prospect routed to sales team.",
            security_flag=injection_detected,
        )

    if tier == "cold":
        return RouteResult(
            route="nurture",
            confidence=0.95,
            reason="Early-stage prospect routed to automated email nurture stream.",
            security_flag=injection_detected,
        )

    # 3. LLM Classifier Fallback (for any ambiguous edge cases)
    if llm_fn:
        try:
            prompt = (
                f"Inbound Lead:\n"
                f"- Channel text:\n{raw[:1000]}\n\n"
                f"- Extracted Intent: {intent}\n"
                f"- Lead Tier: {tier}\n"
                f"- Score: {score_res.score}\n\n"
                f"Determine the correct department: sales, nurture, support, or discard."
            )
            res = llm_fn(
                prompt,
                system="You are an inbound lead router for a software company. Return JSON.",
                max_tokens=150,
                schema=LLMRouteClassification,
            )
            parsed = LLMRouteClassification.model_validate(json.loads(res["text"]))
            return RouteResult(
                route=parsed.route,
                confidence=parsed.confidence,
                reason=f"LLM classifier: {parsed.reason}",
                security_flag=injection_detected,
            )
        except Exception:
            pass

    # Safe default
    return RouteResult(
        route="nurture" if tier != "none" else "discard",
        confidence=0.7,
        reason="Default fallback routing.",
        security_flag=injection_detected,
    )
