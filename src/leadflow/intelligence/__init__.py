"""Autonomous Web Intelligence & 360° Account Dossier Package."""
from .crawler import crawl_domain, extract_domain_from_email, is_freemail_domain, CrawlResult
from .technographics import fingerprint_technographics, detect_technographics_from_html, TechnographicProfile
from .dossier import build_account_dossier, AccountDossier

__all__ = [
    "crawl_domain",
    "extract_domain_from_email",
    "is_freemail_domain",
    "CrawlResult",
    "fingerprint_technographics",
    "detect_technographics_from_html",
    "TechnographicProfile",
    "build_account_dossier",
    "AccountDossier",
]
