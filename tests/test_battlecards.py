"""Phase 3: Sales Battlecard Generator Unit Tests."""
from __future__ import annotations
import pytest

from leadflow.intelligence.battlecard import (
    generate_sales_battlecard,
    detect_competitor,
    SalesBattlecard,
)
from leadflow.intelligence.dossier import AccountDossier


def test_detect_known_competitors():
    # Detect Datadog
    txt1 = "We are currently using Datadog for Kubernetes monitoring but the bills are insane."
    c1 = detect_competitor(txt1)
    assert c1 is not None
    assert c1["name"] == "Datadog"
    assert "ingestion bills" in c1["weakness"]

    # Detect Salesforce
    txt2 = "Looking to replace our Salesforce setup with something lighter."
    c2 = detect_competitor(txt2)
    assert c2 is not None
    assert c2["name"] == "Salesforce"

    # Detect Apollo
    txt3 = "We tried Apollo enrichment webhooks but they time out."
    c3 = detect_competitor(txt3)
    assert c3 is not None
    assert c3["name"] == "Apollo"

    # No competitor
    txt4 = "Hi, we need 50 licenses for our sales team. What is your pricing?"
    assert detect_competitor(txt4) is None


def test_custom_tenant_competitor_override():
    custom_list = ["CrowdStrike", "Wiz", "SentinelOne"]
    txt = "We are currently evaluating Wiz and CrowdStrike for our cloud security."
    detected = detect_competitor(txt, custom_competitors=custom_list)
    assert detected is not None
    assert detected["name"] in ("Wiz", "CrowdStrike")


def test_battlecard_generation_when_competitor_present():
    lead_text = "We currently use HubSpot for sales, but our contact tiers keep doubling our bill. Can LeadFlow help?"
    card = generate_sales_battlecard(
        lead_text=lead_text,
        brand_pitch="Uncapped lead qualification at $0.0002 per lead.",
    )

    assert isinstance(card, SalesBattlecard)
    assert card.competitor_detected == "HubSpot"
    assert card.battlecard_type == "competitor_replacement"
    assert "HubSpot" in card.prospect_pain_trigger
    assert len(card.discovery_questions) == 3
    assert len(card.objection_talk_tracks) >= 1
    assert len(card.recommended_cadence) == 3
    assert card.recommended_cadence[0]["channel"] == "email"
    assert card.recommended_cadence[1]["channel"] == "linkedin"
    assert "Uncapped" in card.winning_differentiator


def test_battlecard_generation_when_no_competitor_present():
    lead_text = "Hi team, we need pricing for 100 enterprise users by next quarter."
    dossier = AccountDossier(
        domain="fintechcorp.io",
        company_name="FintechCorp",
        headline="Fast payment rails for global banks",
        estimated_scale="Enterprise (500+ employees)",
        technographics={"detected_tools": ["Stripe", "Next.js"]},
    )

    card = generate_sales_battlecard(
        lead_text=lead_text,
        dossier=dossier,
        brand_pitch="Enterprise autonomous lead engine.",
    )

    assert isinstance(card, SalesBattlecard)
    assert card.competitor_detected is None
    assert card.battlecard_type == "status_quo_disruption"
    assert "FintechCorp" in card.prospect_pain_trigger
    assert "Status Quo" in card.competitor_weakness
    assert len(card.discovery_questions) == 3
    assert len(card.recommended_cadence) == 3


def test_draft_context_incorporates_battlecard_and_dossier():
    from leadflow.workflow.draft import _build_context
    from leadflow.workflow.dedupe import DedupeResult
    from leadflow.workflow.scoring import ScoreResult

    raw = "We use HubSpot but it is getting too costly for our scale."
    ext = {"first_name": "Sarah", "last_name": "Connor", "company_name": "Cyberdyne", "intent": "demo_request"}
    score = ScoreResult(score=85, tier="hot", reasons=["explicit competitor pain"])
    dedupe = DedupeResult(decision="new")

    dossier = AccountDossier(
        domain="cyberdyne.com",
        company_name="Cyberdyne Systems",
        headline="AI & Autonomous Systems",
        estimated_scale="Enterprise (500+ employees)",
        technographics={"detected_tools": ["HubSpot", "AWS"]},
    )
    card = generate_sales_battlecard(lead_text=raw, dossier=dossier)

    ctx = _build_context(raw, ext, dedupe, score, dossier=dossier, battlecard=card)

    assert "Cyberdyne" in ctx
    assert "<account_dossier>" in ctx
    assert "Tech Stack: HubSpot, AWS" in ctx
    assert "<sales_battlecard>" in ctx
    assert "HubSpot" in ctx

