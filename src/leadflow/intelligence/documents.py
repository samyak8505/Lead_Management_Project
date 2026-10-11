"""Omnichannel Document & PDF RFP Ingestion and Requirement Extraction."""
from __future__ import annotations
import re
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class DocumentRequirements:
    seat_count: Optional[int] = None
    budget_amount: Optional[float] = None
    deadline: Optional[str] = None
    compliance_needs: list[str] = field(default_factory=list)
    integration_needs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentAnalysisResult:
    filename: str
    document_type: str  # "pdf_rfp" | "text_spec" | "markdown_deck" | "json_spec"
    raw_text: str
    requirements: DocumentRequirements
    summary_excerpt: str = ""
    estimated_deal_value: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "document_type": self.document_type,
            "requirements": self.requirements.to_dict(),
            "summary_excerpt": self.summary_excerpt[:400],
            "estimated_deal_value": self.estimated_deal_value,
        }


def extract_text_from_pdf_bytes(pdf_data: bytes) -> str:
    """Robust zero-dependency text stream extractor for standard PDF documents."""
    text_chunks: list[str] = []

    # 1. Check for stream objects
    stream_pattern = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
    for match in stream_pattern.finditer(pdf_data):
        stream_content = match.group(1)
        decompressed = b""
        try:
            decompressed = zlib.decompress(stream_content)
        except Exception:
            decompressed = stream_content  # uncompressed stream

        # Look for text bracket operators: (text) Tj or [(text)] TJ
        tj_matches = re.findall(rb"\((.*?)\)\s*Tj", decompressed)
        for tj in tj_matches:
            try:
                txt = tj.decode("utf-8", errors="ignore").strip()
                if txt and len(txt) > 1:
                    text_chunks.append(txt)
            except Exception:
                pass

        array_tj = re.findall(rb"\[(.*?)\]\s*TJ", decompressed)
        for arr in array_tj:
            inner_texts = re.findall(rb"\((.*?)\)", arr)
            combined = "".join(t.decode("utf-8", errors="ignore") for t in inner_texts).strip()
            if combined and len(combined) > 1:
                text_chunks.append(combined)

    # 2. Fallback: plaintext extraction from raw document if streams weren't matched
    if not text_chunks:
        raw_str = pdf_data.decode("latin-1", errors="ignore")
        # Extract readable alphanumeric sentences
        readable = re.findall(r"\b[A-Za-z0-9,.\$\- ]{15,}\b", raw_str)
        text_chunks.extend(readable[:30])

    return "\n".join(text_chunks).strip()


def parse_requirements_from_text(text: str) -> DocumentRequirements:
    """Extract seat counts, explicit budgets, compliance mandates, and integrations."""
    reqs = DocumentRequirements()
    text_lower = text.lower()

    # 1. Seat / License count
    seat_match = re.search(r"\b(\d{1,5})\s*(?:seats?|licenses?|users?|nodes?|endpoints?)\b", text_lower)
    if seat_match:
        reqs.seat_count = int(seat_match.group(1))

    # 2. Budget Amount
    budget_match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(k|thousand|m|million|b|billion)?\b", text, re.IGNORECASE)
    if budget_match:
        raw_num = budget_match.group(1).replace(",", "")
        multiplier = 1.0
        unit = (budget_match.group(2) or "").lower()
        if unit in ("k", "thousand"):
            multiplier = 1000.0
        elif unit in ("m", "million"):
            multiplier = 1000000.0
        try:
            reqs.budget_amount = float(raw_num) * multiplier
        except ValueError:
            pass

    # 3. Deadline / Timeline
    timeline_match = re.search(r"\b(?:by|before|within|deadline:?)\s+([a-zA-Z0-9\s]{3,25}(?:quarter|q[1-4]|month|weeks?|november|december|october|2026))\b", text_lower)
    if timeline_match:
        reqs.deadline = timeline_match.group(1).strip()

    # 4. Compliance Mandates
    compliance_keywords = ["SOC-2", "SOC 2", "HIPAA", "GDPR", "ISO 27001", "PCI-DSS", "FedRAMP"]
    for comp in compliance_keywords:
        if re.search(r"\b" + re.escape(comp) + r"\b", text, re.IGNORECASE):
            clean_comp = "SOC-2" if "soc" in comp.lower() else comp.upper()
            if clean_comp not in reqs.compliance_needs:
                reqs.compliance_needs.append(clean_comp)

    # 5. Integration Requirements
    integrations = ["Salesforce", "HubSpot", "Kubernetes", "AWS", "Slack", "Stripe", "PostgreSQL", "Jira"]
    for integ in integrations:
        if re.search(r"\b" + re.escape(integ) + r"\b", text, re.IGNORECASE):
            if integ not in reqs.integration_needs:
                reqs.integration_needs.append(integ)

    return reqs


def ingest_document(
    file_bytes: bytes,
    filename: str = "document.pdf",
) -> DocumentAnalysisResult:
    """Ingest uploaded PDF RFP or text specification and extract structured criteria."""
    fn_lower = filename.lower()
    doc_type = "pdf_rfp" if fn_lower.endswith(".pdf") else "text_spec"

    if fn_lower.endswith(".pdf"):
        extracted = extract_text_from_pdf_bytes(file_bytes)
        # If extraction was empty, fallback to clean latin-1 text
        if not extracted:
            extracted = file_bytes.decode("latin-1", errors="ignore")[:3000]
    else:
        extracted = file_bytes.decode("utf-8", errors="ignore")

    reqs = parse_requirements_from_text(extracted)

    # Estimate deal value based on explicit budget or seat count ($600/seat default annual)
    deal_value = reqs.budget_amount
    if not deal_value and reqs.seat_count:
        deal_value = float(reqs.seat_count * 600)

    # First clean paragraph as summary excerpt
    paragraphs = [p.strip() for p in extracted.split("\n") if len(p.strip()) > 30]
    excerpt = paragraphs[0] if paragraphs else extracted[:300]

    return DocumentAnalysisResult(
        filename=filename,
        document_type=doc_type,
        raw_text=extracted,
        requirements=reqs,
        summary_excerpt=excerpt,
        estimated_deal_value=deal_value,
    )
