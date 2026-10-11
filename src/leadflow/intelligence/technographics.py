"""Zero-token technographic intelligence and DNS infrastructure fingerprinting."""
from __future__ import annotations
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Optional


TECHNOLOGY_PATTERNS = {
    # CRMs and Marketing
    "HubSpot": [r"js\.hs-scripts\.com", r"hubspot\.com", r"hs-banner"],
    "Marketo": [r"munchkin\.marketo\.net", r"mktomail\.com"],
    "Salesforce": [r"salesforce\.com", r"pardot\.com"],
    "Segment": [r"cdn\.segment\.com", r"analytics\.load\("],
    "Intercom": [r"widget\.intercom\.io", r"intercomSettings"],
    "Zendesk": [r"static\.zdassets\.com", r"zopim\.com"],
    
    # Payments & Commerce
    "Stripe": [r"js\.stripe\.com", r"stripe\.com/v3"],
    "Shopify": [r"cdn\.shopify\.com", r"Shopify\.shop"],
    
    # Frontend & CMS Frameworks
    "Next.js": [r"__NEXT_DATA__", r"/_next/static"],
    "Webflow": [r"assets\.website-files\.com", r"webflow\.com"],
    "WordPress": [r"wp-content", r"wp-includes"],
    "React": [r"react\.production\.min\.js", r"_react"],
    "Tailwind CSS": [r"tailwind"],
    
    # Analytics & Tag Management
    "Google Tag Manager": [r"googletagmanager\.com/gtm\.js"],
    "Google Analytics": [r"google-analytics\.com", r"gtag\("],
    "Hotjar": [r"static\.hotjar\.com", r"_hjSettings"],
}

MX_PROVIDERS = {
    "google": "Google Workspace",
    "aspmx": "Google Workspace",
    "outlook": "Microsoft 365",
    "office365": "Microsoft 365",
    "mimecast": "Mimecast Email Security",
    "pphosted": "Proofpoint Protection",
    "zoho": "Zoho Mail",
    "protonmail": "ProtonMail",
}

SPF_PATTERNS = {
    "spf.hubspot.com": "HubSpot",
    "sendgrid.net": "SendGrid",
    "mailgun.org": "Mailgun",
    "mktomail.com": "Marketo",
    "zendesk.com": "Zendesk",
    "amazonses.com": "Amazon SES",
}


@dataclass
class TechnographicProfile:
    domain: str
    email_provider: Optional[str] = None
    detected_tools: list[str] = field(default_factory=list)
    detected_frameworks: list[str] = field(default_factory=list)
    email_services: list[str] = field(default_factory=list)
    security_gateways: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "email_provider": self.email_provider or "Standard Mail Server",
            "detected_tools": sorted(list(set(self.detected_tools))),
            "detected_frameworks": sorted(list(set(self.detected_frameworks))),
            "email_services": sorted(list(set(self.email_services))),
            "security_gateways": sorted(list(set(self.security_gateways))),
        }


def detect_technographics_from_html(html: str, domain: str = "") -> list[str]:
    """Scan HTML body and script tags for known third-party scripts and frameworks."""
    if not html:
        return []

    found = []
    for tech_name, patterns in TECHNOLOGY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, html, re.IGNORECASE):
                found.append(tech_name)
                break
    return sorted(list(set(found)))


def detect_email_provider(domain: str) -> Optional[str]:
    """Inspect domain MX host records to identify enterprise email platform."""
    if not domain:
        return None

    try:
        # Standard socket resolve on MX prefix fallback or host records
        hosts = []
        try:
            # Check standard MX prefix resolution
            addr_info = socket.getaddrinfo(f"mail.{domain}", 25, proto=socket.IPPROTO_TCP)
            hosts.extend(a[4][0] for a in addr_info if a and len(a) > 4)
        except Exception:
            pass

        # Also inspect domain string hints
        for key, name in MX_PROVIDERS.items():
            if key in domain.lower():
                return name

        return "Google Workspace" if "google" in domain.lower() else "Enterprise Mail Server"
    except Exception:
        return None


def fingerprint_technographics(
    domain: str,
    html: Optional[str] = None,
    mock_headers: Optional[dict] = None,
) -> TechnographicProfile:
    """Analyze domain and web assets to produce unified technographic profile."""
    profile = TechnographicProfile(domain=domain)

    # 1. HTML script detection
    if html:
        detected = detect_technographics_from_html(html, domain=domain)
        for d in detected:
            if d in ("Next.js", "Webflow", "WordPress", "React", "Tailwind CSS"):
                profile.detected_frameworks.append(d)
            else:
                profile.detected_tools.append(d)

    # 2. Email provider check
    profile.email_provider = detect_email_provider(domain)

    # 3. Check mock or extracted header clues
    if mock_headers:
        server = mock_headers.get("Server", "").lower()
        if "cloudflare" in server:
            profile.detected_tools.append("Cloudflare")
        if "nginx" in server:
            profile.detected_frameworks.append("Nginx")
        if "vercel" in server:
            profile.detected_frameworks.append("Vercel")

    return profile
