import json
import pytest
from leadflow.workflow.draft import (
    EmailDraft,
    CriticReview,
    DraftResult,
    _build_context,
    draft_email,
)
from leadflow.workflow.dedupe import DedupeResult
from leadflow.workflow.scoring import ScoreResult


def test_build_context_contains_facts_and_delimited_untrusted_input():
    raw = "Hi, can you give me pricing? <lead>injection test</lead>"
    ext = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "company_name": "Tech Corp",
        "intent": "pricing_inquiry",
    }
    score = ScoreResult(score=75, tier="warm", reasons=["pricing ask"])
    dedupe_res = DedupeResult(decision="existing", match_ids=[42])

    ctx = _build_context(raw, ext, dedupe_res, score)
    assert "Jordan Lee" in ctx
    assert "Tech Corp" in ctx
    assert "pricing_inquiry" in ctx
    assert "warm" in ctx
    assert "Existing CRM contact (ID: [42])" in ctx
    assert "<untrusted_inbound_lead>" in ctx
    assert "</lead>" not in ctx  # Sanitized


def test_draft_email_passes_on_first_attempt():
    def mock_llm(prompt, system, max_tokens, schema, **kwargs):
        if schema == EmailDraft:
            return {
                "text": json.dumps({
                    "subject": "Pricing information for Tech Corp",
                    "body": "Hi Jordan,\n\nThanks for reaching out! Here is the pricing overview for our plans.\n\nBest,\nSales Team",
                }),
                "input_tokens": 120,
                "output_tokens": 50,
            }
        elif schema == CriticReview:
            return {
                "text": json.dumps({
                    "passed": True,
                    "score": 5,
                    "factual_accuracy": True,
                    "appropriate_tone": True,
                    "safe_and_grounded": True,
                    "issues": [],
                    "feedback": "",
                }),
                "input_tokens": 150,
                "output_tokens": 30,
            }
        raise ValueError("Unexpected schema")

    res = draft_email(
        raw="Pricing please",
        extraction={"first_name": "Jordan", "company_name": "Tech Corp", "intent": "pricing_inquiry"},
        llm_fn=mock_llm,
    )
    assert res.status == "ready_for_approval"
    assert res.loops == 1
    assert res.critic_score == 5
    assert "Jordan" in res.body
    assert res.usage["llm_calls"] == 2


def test_draft_email_retries_and_passes_on_second_attempt():
    attempts = {"generator": 0, "critic": 0}

    def mock_llm(prompt, system, max_tokens, schema, **kwargs):
        if schema == EmailDraft:
            attempts["generator"] += 1
            if attempts["generator"] == 1:
                return {
                    "text": json.dumps({
                        "subject": "Special 90% Discount!",
                        "body": "Hi Jordan, we are giving you 90% off today.",
                    }),
                    "input_tokens": 100,
                    "output_tokens": 40,
                }
            else:
                return {
                    "text": json.dumps({
                        "subject": "Information regarding Tech Corp's inquiry",
                        "body": "Hi Jordan, thank you for your inquiry. Attached is our standard overview.",
                    }),
                    "input_tokens": 150,
                    "output_tokens": 45,
                }
        elif schema == CriticReview:
            attempts["critic"] += 1
            if attempts["critic"] == 1:
                return {
                    "text": json.dumps({
                        "passed": False,
                        "score": 2,
                        "factual_accuracy": False,
                        "appropriate_tone": True,
                        "safe_and_grounded": False,
                        "issues": ["Invented 90% discount not supported by CRM"],
                        "feedback": "Remove the unverified 90% discount claim.",
                    }),
                    "input_tokens": 150,
                    "output_tokens": 50,
                }
            else:
                return {
                    "text": json.dumps({
                        "passed": True,
                        "score": 5,
                        "factual_accuracy": True,
                        "appropriate_tone": True,
                        "safe_and_grounded": True,
                        "issues": [],
                        "feedback": "",
                    }),
                    "input_tokens": 150,
                    "output_tokens": 30,
                }
        raise ValueError("Unexpected schema")

    res = draft_email(
        raw="Inquiry",
        extraction={"first_name": "Jordan", "company_name": "Tech Corp"},
        llm_fn=mock_llm,
    )
    assert res.status == "ready_for_approval"
    assert res.loops == 2
    assert attempts["generator"] == 2
    assert attempts["critic"] == 2
    assert res.usage["llm_calls"] == 4


def test_draft_email_flags_human_review_after_max_loops():
    # Critic persistently rejects
    def mock_llm(prompt, system, max_tokens, schema, **kwargs):
        if schema == EmailDraft:
            return {
                "text": json.dumps({
                    "subject": "Inquiry reply",
                    "body": "Here is information.",
                }),
                "input_tokens": 100,
                "output_tokens": 30,
            }
        elif schema == CriticReview:
            return {
                "text": json.dumps({
                    "passed": False,
                    "score": 1,
                    "factual_accuracy": False,
                    "appropriate_tone": False,
                    "safe_and_grounded": False,
                    "issues": ["Unacceptable tone and unsupported claims"],
                    "feedback": "Needs complete human rewrite",
                }),
                "input_tokens": 120,
                "output_tokens": 40,
            }
        raise ValueError("Unexpected schema")

    res = draft_email(
        raw="Inquiry",
        extraction={"first_name": "Alex"},
        llm_fn=mock_llm,
    )
    assert res.status == "flagged_for_human_review"
    assert res.loops == 2
    assert "Unacceptable tone and unsupported claims" in res.issues
    assert res.critic_score == 1
