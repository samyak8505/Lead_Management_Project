"""Phase 3: Omnichannel Document & PDF RFP Ingestion Unit Tests."""
from __future__ import annotations
import zlib
import pytest

from leadflow.intelligence.documents import (
    parse_requirements_from_text,
    extract_text_from_pdf_bytes,
    ingest_document,
    DocumentRequirements,
    DocumentAnalysisResult,
)


def test_parse_requirements_from_text():
    sample_rfp = """
    Enterprise RFP - Autonomous RevOps Ingestion
    Overview:
    Our organization is looking to onboard 250 seats across EMEA and North America.
    We have an allocated annual budget of $120k for this initiative.
    The solution must achieve SOC-2 and HIPAA compliance before deployment.
    Required integrations: Salesforce, Slack, and AWS.
    Target deployment: by Q4 2026.
    """
    reqs = parse_requirements_from_text(sample_rfp)

    assert reqs.seat_count == 250
    assert reqs.budget_amount == 120000.0
    assert "SOC-2" in reqs.compliance_needs
    assert "HIPAA" in reqs.compliance_needs
    assert "Salesforce" in reqs.integration_needs
    assert "Slack" in reqs.integration_needs
    assert "AWS" in reqs.integration_needs
    assert reqs.deadline is not None
    assert "q4 2026" in reqs.deadline.lower()


def test_parse_budget_multipliers():
    # Test 'k'
    reqs1 = parse_requirements_from_text("Allocated budget is $50k for this project.")
    assert reqs1.budget_amount == 50000.0

    # Test 'million' / 'm'
    reqs2 = parse_requirements_from_text("We have a $2.5m transformation program.")
    assert reqs2.budget_amount == 2500000.0

    # Test direct comma notation
    reqs3 = parse_requirements_from_text("Annual licensing cap is $85,000.")
    assert reqs3.budget_amount == 85000.0


def test_deal_value_calculation():
    # Case 1: Explicit budget takes precedence
    text1 = "We have 50 licenses and a budget of $60k."
    doc1 = ingest_document(text1.encode("utf-8"), filename="rfp.txt")
    assert doc1.estimated_deal_value == 60000.0

    # Case 2: Inferred from seat count when no explicit budget ($600/seat)
    text2 = "We need 100 seats for our inside sales representatives."
    doc2 = ingest_document(text2.encode("utf-8"), filename="seats_only.md")
    assert doc2.requirements.seat_count == 100
    assert doc2.requirements.budget_amount is None
    assert doc2.estimated_deal_value == 60000.0  # 100 * 600


def test_pdf_extraction_and_ingestion():
    # Build a synthetic PDF containing compressed text stream
    stream_inner = b"BT\n/F1 12 Tf\n(Enterprise procurement for 75 licenses.) Tj\nET"
    compressed = zlib.compress(stream_inner)

    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Length " + str(len(compressed)).encode() + b" /Filter /FlateDecode >>\n"
        b"stream\r\n" + compressed + b"\r\nendstream\n"
        b"endobj\n%%EOF"
    )

    extracted_text = extract_text_from_pdf_bytes(pdf_bytes)
    assert "Enterprise procurement for 75 licenses." in extracted_text

    doc = ingest_document(pdf_bytes, filename="vendor_rfp.pdf")
    assert doc.document_type == "pdf_rfp"
    assert doc.requirements.seat_count == 75
    assert doc.estimated_deal_value == 45000.0  # 75 * 600
    assert doc.to_dict()["filename"] == "vendor_rfp.pdf"
