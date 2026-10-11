"""Lightweight autonomous domain crawler with hard timeout safeguards."""
from __future__ import annotations
import html
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("leadflow.intelligence.crawler")

FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "icloud.com", "me.com", "aol.com",
    "mail.com", "proton.me", "protonmail.com", "zoho.com", "gmx.com",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 (compatible; LeadFlow-Agent/1.0)"


def extract_domain_from_email(email: str) -> Optional[str]:
    """Extract clean domain name from email address."""
    if not email or "@" not in email:
        return None
    parts = email.strip().split("@")
    if len(parts) != 2:
        return None
    domain = parts[1].strip().lower()
    # Strip any trailing angle brackets or periods
    domain = re.sub(r"[>\s\.]+$", "", domain)
    return domain if "." in domain else None


def is_freemail_domain(domain: str) -> bool:
    """Check if domain belongs to a consumer freemail service."""
    if not domain:
        return True
    return domain.strip().lower() in FREEMAIL_DOMAINS


@dataclass
class CrawlResult:
    domain: str
    status: str  # "success" | "timeout" | "unreachable" | "freemail"
    title: Optional[str] = None
    description: Optional[str] = None
    headings: list[str] = field(default_factory=list)
    clean_text: str = ""
    raw_html: Optional[str] = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "headings": self.headings[:5],
            "clean_text_preview": self.clean_text[:300] if self.clean_text else "",
            "duration_ms": self.duration_ms,
        }


def clean_html(raw_html: str) -> tuple[Optional[str], Optional[str], list[str], str]:
    """Extract title, description, headings, and clean readable text from HTML."""
    if not raw_html:
        return None, None, [], ""

    # 1. Extract Title
    title = None
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = html.unescape(title_match.group(1)).strip()

    # 2. Extract Meta Description
    desc = None
    desc_match = re.search(
        r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']',
        raw_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not desc_match:
        desc_match = re.search(
            r'<meta\s+[^>]*content=["\'](.*?)["\'][^>]*name=["\']description["\']',
            raw_html,
            re.IGNORECASE | re.DOTALL,
        )
    if desc_match:
        desc = html.unescape(desc_match.group(1)).strip()

    # 3. Extract Headings (h1, h2)
    headings = []
    for h in re.finditer(r"<h[1-2][^>]*>(.*?)</h[1-2]>", raw_html, re.IGNORECASE | re.DOTALL):
        txt = re.sub(r"<[^>]+>", "", h.group(1)).strip()
        if txt and len(txt) > 3:
            headings.append(html.unescape(txt))

    # 4. Remove scripts, styles, and SVG
    cleaned = re.sub(r"<(script|style|svg|noscript)[^>]*>.*?</\1>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return title, desc, headings, cleaned[:2000]


def crawl_domain(domain: str, timeout_seconds: float = 2.5) -> CrawlResult:
    """Fetch company website and extract core business positioning with hard timeout."""
    if not domain or is_freemail_domain(domain):
        return CrawlResult(domain=domain or "", status="freemail")

    t0 = time.perf_counter()
    url = f"https://{domain}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return CrawlResult(domain=domain, status="unreachable", duration_ms=round((time.perf_counter() - t0) * 1000, 2))

            raw_bytes = resp.read(250000)  # Read up to 250KB max to avoid huge downloads
            charset = resp.headers.get_content_charset() or "utf-8"
            raw_html = raw_bytes.decode(charset, errors="ignore")

            title, desc, headings, clean_txt = clean_html(raw_html)
            dur = round((time.perf_counter() - t0) * 1000, 2)
            return CrawlResult(
                domain=domain,
                status="success",
                title=title,
                description=desc,
                headings=headings,
                clean_text=clean_txt,
                raw_html=raw_html,
                duration_ms=dur,
            )
    except (TimeoutError, urllib.error.URLError, Exception) as e:
        dur = round((time.perf_counter() - t0) * 1000, 2)
        log.warning("Crawl failed for domain %s: %s (after %.1fms)", domain, e, dur)
        return CrawlResult(domain=domain, status="timeout" if "timed out" in str(e).lower() else "unreachable", duration_ms=dur)
