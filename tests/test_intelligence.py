"""Phase 2: Autonomous Web Intelligence & 360° Account Dossier Unit Tests."""
from __future__ import annotations
import pytest

from leadflow.intelligence import (
    crawl_domain,
    extract_domain_from_email,
    is_freemail_domain,
    detect_technographics_from_html,
    fingerprint_technographics,
    build_account_dossier,
    AccountDossier,
)
from leadflow.intelligence.crawler import clean_html


def test_domain_extraction_from_email():
    assert extract_domain_from_email("alex@vanceglobal.com") == "vanceglobal.com"
    assert extract_domain_from_email("  Sarah.Chen@CloudGuard.io  ") == "cloudguard.io"
    assert extract_domain_from_email("<mark@fintech-demo.org>") == "fintech-demo.org"
    assert extract_domain_from_email("invalid-email-no-at") is None
    assert extract_domain_from_email("") is None


def test_freemail_domain_detection():
    assert is_freemail_domain("gmail.com") is True
    assert is_freemail_domain("yahoo.com") is True
    assert is_freemail_domain("hotmail.com") is True
    assert is_freemail_domain("proton.me") is True
    assert is_freemail_domain("quantumdynamics.io") is False
    assert is_freemail_domain("vanceglobal.com") is False


def test_clean_html_extraction():
    sample_html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>Quantum Dynamics | Autonomous Cloud Robotics</title>
        <meta name="description" content="Next-generation autonomous robotics platform for healthcare and logistics.">
        <script>console.log("analytics");</script>
        <style>body { color: red; }</style>
      </head>
      <body>
        <h1>Revolutionizing Healthcare Robotics</h1>
        <h2>Enterprise Fleet Management</h2>
        <p>Quantum Dynamics powers 10,000 autonomous hospital rovers across North America.</p>
      </body>
    </html>
    """
    title, desc, headings, text = clean_html(sample_html)
    assert title == "Quantum Dynamics | Autonomous Cloud Robotics"
    assert "autonomous robotics platform" in desc
    assert len(headings) == 2
    assert "Revolutionizing Healthcare Robotics" in headings[0]
    assert "Enterprise Fleet Management" in headings[1]
    assert "hospital rovers" in text
    assert "console.log" not in text
    assert "body { color" not in text


def test_technographic_script_detection():
    sample_scripts = """
    <html>
      <head>
        <script src="https://js.hs-scripts.com/12345.js"></script>
        <script src="https://js.stripe.com/v3/"></script>
        <script src="https://www.googletagmanager.com/gtm.js?id=GTM-XYZ"></script>
      </head>
      <body>
        <div id="__NEXT_DATA__">{}</div>
      </body>
    </html>
    """
    tools = detect_technographics_from_html(sample_scripts)
    assert "HubSpot" in tools
    assert "Stripe" in tools
    assert "Google Tag Manager" in tools
    assert "Next.js" in tools


def test_technographic_fingerprinting_summary():
    html_code = """<script src="https://cdn.segment.com/analytics.js"></script>"""
    headers = {"Server": "cloudflare"}
    profile = fingerprint_technographics("example-startup.io", html=html_code, mock_headers=headers)

    d = profile.to_dict()
    assert d["domain"] == "example-startup.io"
    assert "Segment" in d["detected_tools"]
    assert "Cloudflare" in d["detected_tools"]


def test_crawler_handles_unreachable_domain_gracefully():
    # Attempting an invalid domain should not raise, must return status unreachable
    res = crawl_domain("non-existent-fake-domain-123456789.invalid", timeout_seconds=1.0)
    assert res.status == "unreachable"
    assert res.domain == "non-existent-fake-domain-123456789.invalid"
    assert res.clean_text == ""


def test_crawler_bypasses_freemail():
    res = crawl_domain("gmail.com")
    assert res.status == "freemail"
    assert res.raw_html is None


def test_account_dossier_synthesis_corporate():
    dossier = build_account_dossier(
        email="elena@vanceglobal.com",
        company_hint="Vance Global",
        target_industries=["Technology", "Software"],
        timeout_seconds=0.5,
    )
    assert isinstance(dossier, AccountDossier)
    assert dossier.domain == "vanceglobal.com"
    assert dossier.company_name == "Vance Global"
    assert dossier.icp_fit_score >= 10
    assert len(dossier.summary_notes) >= 2
    assert "vanceglobal.com" in dossier.summary_notes[0]


def test_account_dossier_synthesis_freemail():
    dossier = build_account_dossier(
        email="alex.investor@gmail.com",
        company_hint=None,
    )
    assert dossier.domain == "gmail.com"
    assert dossier.crawl_status == "freemail"
    assert "Personal email used" in dossier.summary_notes[0]
    assert dossier.estimated_scale == "Individual / Micro (<5)"
