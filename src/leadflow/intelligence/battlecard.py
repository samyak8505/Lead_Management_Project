"""Dynamic Sales Battlecards and Multi-Touch Cadence Generator."""
from __future__ import annotations
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .dossier import AccountDossier

COMPETITOR_INTELLIGENCE_BASE = {
    "salesforce": {
        "name": "Salesforce",
        "weakness": "High per-user licensing ($150+/mo), clunky legacy UI, and requires costly dedicated administrators to customize.",
        "winning_pitch": "Modern, self-serve setup in 5 minutes with zero maintenance overhead and transparent pricing.",
        "discovery_questions": [
            "Are your sales reps actually logging activities daily, or is Salesforce friction causing pipeline blindspots?",
            "How much are you spending annually on custom Salesforce administration and consultants?",
            "What features in Salesforce do your reps actually use versus the 90% that sits unused?",
        ],
        "objection_handling": "Salesforce is a great legacy database, but our platform delivers the automated speed and rep adoption that modern sales teams need to hit quota.",
    },
    "hubspot": {
        "name": "HubSpot",
        "weakness": "Steep pricing escalation as contact lists grow, contact tier paywalls, and limited customization for complex workflows.",
        "winning_pitch": "Uncapped contact scaling without penalizing growth, built specifically for high-velocity qualification.",
        "discovery_questions": [
            "Have you hit HubSpot's tier jumps where adding 5,000 marketing contacts doubles your monthly bill?",
            "How much manual triage do your reps have to do inside HubSpot before working a lead?",
            "Are you looking to replace HubSpot entirely or plug automated qualification directly into your existing CRM?",
        ],
        "objection_handling": "HubSpot excels at inbound marketing, but our automated intelligence engine enriches and qualifies leads 10x faster before your reps touch them.",
    },
    "apollo": {
        "name": "Apollo",
        "weakness": "Slow third-party enrichment webhooks, outdated contact database entries, and deliverability spam flags.",
        "winning_pitch": "Real-time live web crawling and technographic verification with sub-second response times.",
        "discovery_questions": [
            "How often do your reps hit bounced emails or stale phone numbers in Apollo?",
            "What is the revenue cost when an enrichment webhook times out during form submission?",
            "Are you looking for generic contact lists, or live intelligence on prospects actively visiting your site?",
        ],
        "objection_handling": "Apollo provides static database exports; we provide live, real-time verified intelligence on the exact companies actively reaching out to you.",
    },
    "datadog": {
        "name": "Datadog",
        "weakness": "Unpredictable log and metrics ingestion bills that create monthly billing anxiety.",
        "winning_pitch": "Predictable flat-rate pricing with zero surprise overage penalties.",
        "discovery_questions": [
            "How much engineering time does your team spend filtering out logs to avoid Datadog bill spikes?",
            "Have you ever experienced sudden 3x bill spikes during high-traffic incident responses?",
            "Would guaranteed flat-rate scaling simplify your infrastructure budgeting?",
        ],
        "objection_handling": "Datadog penalizes you for generating data; our platform provides predictable flat pricing so you never have to throttle observability.",
    },
    "zoominfo": {
        "name": "ZoomInfo",
        "weakness": "Rigid multi-year enterprise contract lock-ins ($15k–$30k minimums) with aggressive auto-renewal terms.",
        "winning_pitch": "Flexible monthly software with zero lock-in, immediate time-to-value, and autonomous research.",
        "discovery_questions": [
            "Are you currently locked into an annual ZoomInfo contract, and when is your renewal window?",
            "Are your reps actually using the credits you pay for each month?",
            "How would your team benefit from automated account dossiers delivered directly into your workflow?",
        ],
        "objection_handling": "ZoomInfo forces massive annual contracts; we give your reps immediate, accurate account dossiers without six-figure commitments.",
    },
}


@dataclass
class CadenceStep:
    day: int
    channel: str  # "email" | "linkedin" | "phone"
    title: str
    action_prompt: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SalesBattlecard:
    competitor_detected: Optional[str]
    battlecard_type: str  # "competitor_replacement" | "status_quo_disruption" | "high_intent_accelerator"
    prospect_pain_trigger: str
    competitor_weakness: str
    winning_differentiator: str
    discovery_questions: list[str] = field(default_factory=list)
    objection_talk_tracks: list[str] = field(default_factory=list)
    recommended_cadence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_competitor(
    lead_text: str,
    custom_competitors: Optional[list[str]] = None,
) -> Optional[dict]:
    """Scan raw inbound text for known competitor mentions."""
    text_lower = lead_text.lower()
    
    # Check custom tenant competitors first
    if custom_competitors:
        for comp in custom_competitors:
            comp_clean = comp.strip().lower()
            if comp_clean and re.search(r"\b" + re.escape(comp_clean) + r"\b", text_lower):
                # Return custom or base intelligence
                base = COMPETITOR_INTELLIGENCE_BASE.get(comp_clean)
                if base:
                    return base
                return {
                    "name": comp.title(),
                    "weakness": f"Generic legacy platform with high operational overhead compared to our focused solution.",
                    "winning_pitch": f"Purpose-built modern alternative delivering faster time-to-value and personalized support.",
                    "discovery_questions": [
                        f"What specific limitations have you run into with {comp.title()}?",
                        f"How long did it take your team to get fully onboarded with {comp.title()}?",
                        f"What is the single biggest missing capability you wish {comp.title()} offered?",
                    ],
                    "objection_handling": f"While {comp.title()} is a known name, our customers choose us for higher responsiveness, lower TCO, and immediate deployment.",
                }

    # Check universal database
    for key, data in COMPETITOR_INTELLIGENCE_BASE.items():
        if re.search(r"\b" + re.escape(key) + r"\b", text_lower):
            return data

    return None


def generate_sales_battlecard(
    lead_text: str,
    dossier: Optional[AccountDossier] = None,
    custom_competitors: Optional[list[str]] = None,
    brand_pitch: Optional[str] = None,
) -> SalesBattlecard:
    """Generate strategic sales battlecard and 3-touch cadence sequence."""
    comp_data = detect_competitor(lead_text, custom_competitors)
    pitch = brand_pitch or "Automated, grounded lead qualification with zero unapproved writes and instant CRM sync."

    # 1. SCENARIO A: Competitor Mentioned (Replacement Playbook)
    if comp_data:
        comp_name = comp_data["name"]
        return SalesBattlecard(
            competitor_detected=comp_name,
            battlecard_type="competitor_replacement",
            prospect_pain_trigger=f"Evaluating alternative or replacement to {comp_name}.",
            competitor_weakness=comp_data["weakness"],
            winning_differentiator=f"{pitch} Won't suffer from {comp_name}'s {comp_data['weakness'].split(',')[0].lower()}.",
            discovery_questions=comp_data["discovery_questions"],
            objection_talk_tracks=[
                comp_data["objection_handling"],
                f"Most teams migrating from {comp_name} see a 70% reduction in setup overhead within week 1.",
            ],
            recommended_cadence=[
                CadenceStep(day=0, channel="email", title="Competitive Contrast Demo", action_prompt=f"Acknowledge {comp_name} evaluation and invite to architecture demo highlighting our differentiators.").to_dict(),
                CadenceStep(day=2, channel="linkedin", title="LinkedIn Connection Note", action_prompt=f"Connect with decision maker sharing 1-page migration guide from {comp_name}.").to_dict(),
                CadenceStep(day=5, channel="email", title="Migration Case Study", action_prompt=f"Share customer case study of team who switched from {comp_name} to our platform.").to_dict(),
            ],
        )

    # 2. SCENARIO B: No Competitor Mentioned (Status Quo & Intent Playbook)
    company_name = dossier.company_name if dossier else "the prospect's team"
    scale = dossier.estimated_scale if dossier else "Mid-Market"
    tools_hint = ", ".join(dossier.technographics.get("detected_tools", [])) if dossier and dossier.technographics.get("detected_tools") else "manual processes"

    return SalesBattlecard(
        competitor_detected=None,
        battlecard_type="status_quo_disruption",
        prospect_pain_trigger=f"Prospect inquiring about pricing/demo for {company_name}; primary competition is Status Quo (doing nothing or spreadsheets).",
        competitor_weakness=f"Status Quo Friction: Manual copy-pasting and slow lead response times cost {scale} companies over 15 sales hours every week.",
        winning_differentiator=f"{pitch} Instantly automates lead qualification and research before reps even open the email.",
        discovery_questions=[
            f"How many inbound leads does {company_name} receive each week, and what is your current response time?",
            f"How much engineering or sales time is spent manually qualifying leads and looking up company details?",
            "If we could qualify and draft replies for every high-value lead in 45 seconds, how would that impact your quarterly pipeline?",
        ],
        objection_talk_tracks=[
            "Don't just email a generic PDF price sheet (70% ghosting rate). Offer a 15-minute scoping demo to map their exact volume and ROI.",
            f"Reference their current tech stack ({tools_hint}) to show seamless native integration without re-engineering their workflow.",
        ],
        recommended_cadence=[
            CadenceStep(day=0, channel="email", title="Personalized Architecture Demo Offer", action_prompt="Send tailored response addressing specific team needs and proposing a 20-minute walkthrough.").to_dict(),
            CadenceStep(day=2, channel="linkedin", title="Executive Peer Connection", action_prompt=f"Connect on LinkedIn referencing their company focus at {company_name}.").to_dict(),
            CadenceStep(day=5, channel="email", title="Value ROI Follow-up", action_prompt="Follow up with customer benchmark showing 4x faster lead response times.").to_dict(),
        ],
    )
