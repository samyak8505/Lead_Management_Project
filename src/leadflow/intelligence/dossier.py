"""360° Account Dossier Synthesizer combining web crawl and technographic signals."""
from __future__ import annotations
import concurrent.futures
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .crawler import crawl_domain, extract_domain_from_email, is_freemail_domain, CrawlResult
from .technographics import fingerprint_technographics, TechnographicProfile

log = logging.getLogger("leadflow.intelligence.dossier")


@dataclass
class AccountDossier:
    domain: str
    company_name: str
    headline: str
    products_or_services: list[str] = field(default_factory=list)
    target_audience: str = "General Business / Consumers"
    technographics: dict[str, Any] = field(default_factory=dict)
    estimated_scale: str = "Mid-Market (50-250 employees)"
    growth_signals: list[str] = field(default_factory=list)
    icp_fit_score: int = 50
    summary_notes: list[str] = field(default_factory=list)
    crawl_status: str = "success"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _estimate_company_scale(text: str, technographics: dict) -> str:
    """Infer company scale from public copy and detected enterprise software."""
    txt_lower = text.lower()
    tools = set(technographics.get("detected_tools", []))

    if any(k in txt_lower for k in ["fortune 500", "global enterprise", "worldwide offices", "multinational"]) or "Salesforce" in tools or "Marketo" in tools:
        return "Enterprise (500+ employees)"
    elif any(k in txt_lower for k in ["series b", "series c", "hundreds of teams", "mid-size"]):
        return "Mid-Market (100-500 employees)"
    elif any(k in txt_lower for k in ["seed", "series a", "fast-growing startup", "small team"]):
        return "Growth Stage (20-100 employees)"
    elif "Stripe" in tools or "Next.js" in tools:
        return "Modern Tech Startup (10-50 employees)"
    return "Mid-Market (50-250 employees)"


def _extract_company_name_from_domain(domain: str) -> str:
    """Extract clean company name from domain string."""
    if not domain:
        return "Unknown Company"
    name = domain.split(".")[0]
    # Remove common demo suffixes or hyphens
    name = re.sub(r"-(demo|io|app|tech|global|corp)$", "", name, flags=re.IGNORECASE)
    parts = re.split(r"[-_]", name)
    return " ".join(p.capitalize() for p in parts if p)


def build_account_dossier(
    email: Optional[str] = None,
    domain: Optional[str] = None,
    company_hint: Optional[str] = None,
    target_industries: Optional[list[str]] = None,
    timeout_seconds: float = 2.5,
) -> AccountDossier:
    """Synthesize complete 360° Account Dossier from domain crawl and technographic checks."""
    resolved_domain = domain or (extract_domain_from_email(email or "") if email else None)

    # 1. Freemail or Missing Domain Fallback
    if not resolved_domain or is_freemail_domain(resolved_domain):
        comp = company_hint or "Independent / Self-Employed"
        return AccountDossier(
            domain=resolved_domain or "personal_email",
            company_name=comp,
            headline=f"Inbound inquiry from personal email address ({resolved_domain or 'none'})",
            products_or_services=["Consulting / Direct Consumer"],
            target_audience="Individual",
            technographics={"email_provider": "Consumer Webmail", "detected_tools": []},
            estimated_scale="Individual / Micro (<5)",
            growth_signals=["Direct consumer or stealth inquiry"],
            icp_fit_score=30,
            summary_notes=["Personal email used; recommend requesting corporate domain on follow-up."],
            crawl_status="freemail",
        )

    company_name = company_hint or _extract_company_name_from_domain(resolved_domain)

    # 2. Parallel Crawl & Technographic Profiling
    crawl_res = CrawlResult(domain=resolved_domain, status="unreachable")
    tech_prof = TechnographicProfile(domain=resolved_domain)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        crawl_future = executor.submit(crawl_domain, resolved_domain, timeout_seconds)
        try:
            crawl_res = crawl_future.result(timeout=timeout_seconds + 0.5)
        except Exception:
            crawl_res = CrawlResult(domain=resolved_domain, status="timeout")

        tech_prof = fingerprint_technographics(
            resolved_domain,
            html=crawl_res.raw_html,
        )

    # 3. Analyze Business Value Proposition & Products
    headline = crawl_res.description or crawl_res.title or f"{company_name} corporate domain ({resolved_domain})"
    products = []
    if crawl_res.headings:
        products = [h for h in crawl_res.headings[:4] if len(h) < 60]
    if not products and crawl_res.title:
        products = [crawl_res.title]

    techno_dict = tech_prof.to_dict()
    scale = _estimate_company_scale(crawl_res.clean_text, techno_dict)

    # 4. Growth Signals
    growth_signals = []
    if any(f in techno_dict["detected_frameworks"] for f in ["Next.js", "React"]):
        growth_signals.append("Modern modern web stack detected (Next.js/React)")
    if any(t in techno_dict["detected_tools"] for t in ["HubSpot", "Salesforce", "Marketo"]):
        growth_signals.append("Active CRM / Marketing Automation investment")
    if "Stripe" in techno_dict["detected_tools"]:
        growth_signals.append("Active online commercial billing infrastructure")
    if "Google Workspace" in (tech_prof.email_provider or ""):
        growth_signals.append("Google Workspace enterprise organization")

    # 5. ICP Fit Score (0 - 100)
    icp_score = 50  # Base
    if crawl_res.status == "success":
        icp_score += 15
    if techno_dict["detected_tools"]:
        icp_score += 15
    if target_industries and any(ind.lower() in (crawl_res.clean_text + " " + headline).lower() for ind in target_industries):
        icp_score += 20
    icp_score = min(max(icp_score, 10), 100)

    # 6. Rep Summary Notes
    notes = [
        f"Verified corporate domain: {resolved_domain}",
        f"Estimated Scale: {scale}",
        f"Email Platform: {tech_prof.email_provider or 'Standard'}",
    ]
    if techno_dict["detected_tools"]:
        notes.append(f"Tools Detected: {', '.join(techno_dict['detected_tools'])}")

    return AccountDossier(
        domain=resolved_domain,
        company_name=company_name,
        headline=headline,
        products_or_services=products,
        target_audience="Enterprise / B2B Organizations",
        technographics=techno_dict,
        estimated_scale=scale,
        growth_signals=growth_signals,
        icp_fit_score=icp_score,
        summary_notes=notes,
        crawl_status=crawl_res.status,
    )
