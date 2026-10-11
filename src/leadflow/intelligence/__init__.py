"""Autonomous Web Intelligence, Account Dossiers, Battlecards & Document Ingestion."""
from .crawler import crawl_domain, extract_domain_from_email, is_freemail_domain, CrawlResult
from .technographics import fingerprint_technographics, detect_technographics_from_html, TechnographicProfile
from .dossier import build_account_dossier, AccountDossier
from .battlecard import generate_sales_battlecard, detect_competitor, SalesBattlecard, CadenceStep
from .documents import ingest_document, parse_requirements_from_text, DocumentAnalysisResult, DocumentRequirements

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
    "generate_sales_battlecard",
    "detect_competitor",
    "SalesBattlecard",
    "CadenceStep",
    "ingest_document",
    "parse_requirements_from_text",
    "DocumentAnalysisResult",
    "DocumentRequirements",
]
