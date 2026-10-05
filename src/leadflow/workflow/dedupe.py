"""Step 6: Duplicate and match step against CRM.

Hierarchy:
1. Deterministic exact email matching (normalized, case-insensitive).
2. Fuzzy & domain-based heuristic matching:
   - Nickname + same company domain -> 'existing'
   - Same person + company, but unseen/new email domain -> 'needs_review'
   - Same person + company, web form with no email -> 'needs_review'
   - Name collision guard: same name, different company/domain -> 'new'
3. LLM disambiguation fallback for ambiguous/borderline candidate matches.

Returns DedupeResult with decision ('new' | 'existing' | 'needs_review'),
matched contact IDs, confidence score, and explainable reasons.
"""
from __future__ import annotations
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from leadflow import llm as _llm
from leadflow.crm import SQLiteCRM

Decision = Literal["new", "existing", "needs_review"]

LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp",
    "corporation", "co", "pvt", "private", "gmbh", "sa", "plc"
}

NICKNAMES = {
    "robert": {"bob", "bobby", "rob", "robbie"},
    "bob": {"robert", "bobby", "rob", "robbie"},
    "william": {"bill", "billy", "will", "willy", "liam"},
    "bill": {"william", "billy", "will", "willy"},
    "elizabeth": {"liz", "lizzie", "beth", "eliza"},
    "liz": {"elizabeth", "lizzie", "beth", "eliza"},
    "michael": {"mike", "mikey"},
    "mike": {"michael", "mikey"},
    "jennifer": {"jen", "jenny"},
    "jen": {"jennifer", "jenny"},
    "christopher": {"chris"},
    "chris": {"christopher", "christian"},
    "katherine": {"kate", "katie", "kathy"},
    "kate": {"katherine", "katie", "kathy"},
    "richard": {"rick", "rich", "ricky", "dick"},
    "rick": {"richard", "rich", "ricky"},
    "james": {"jim", "jimmy"},
    "jim": {"james", "jimmy"},
    "thomas": {"tom", "tommy"},
    "tom": {"thomas", "tommy"},
    "alexander": {"alex"},
    "alex": {"alexander", "alexandra"},
    "jonathan": {"jon", "john"},
    "jon": {"jonathan", "john"},
    "daniel": {"dan", "danny"},
    "dan": {"daniel", "danny"},
    "david": {"dave"},
    "dave": {"david"},
    "matthew": {"matt"},
    "matt": {"matthew"},
    "steven": {"steve", "stephen"},
    "steve": {"steven", "stephen"},
    "anthony": {"tony"},
    "tony": {"anthony"},
    "joseph": {"joe", "joey"},
    "joe": {"joseph", "joey"},
    "benjamin": {"ben", "benny"},
    "ben": {"benjamin", "benny"},
    "andrew": {"andy", "drew"},
    "andy": {"andrew", "drew"},
    "samuel": {"sam", "samy"},
    "sam": {"samuel", "samantha"},
}


class LLMDisambiguation(BaseModel):
    decision: Decision = Field(description="Dedupe decision: existing, new, or needs_review")
    matched_contact_id: Optional[int] = Field(description="Matched CRM contact ID if existing or needs_review, else null")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")
    rationale: str = Field(description="Brief explanation of why this decision was reached")


@dataclass
class DedupeResult:
    decision: Decision
    match_ids: list[int] = field(default_factory=list)
    confidence: float = 1.0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "match_ids": self.match_ids,
            "confidence": round(self.confidence, 4),
            "reasons": self.reasons,
        }


# ---------------- Normalization & Matching Helpers ----------------

def _plain(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()


def norm_email(s: str | None) -> str:
    if not s:
        return ""
    return str(s).strip().strip("<>").strip().lower()


def norm_name(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(re.sub(r"[^\w\s'-]", " ", _plain(s)).split())


def norm_company(s: str | None) -> str:
    if not s:
        return ""
    s = _plain(s).replace("&", " and ")
    toks = [t for t in re.sub(r"[^\w\s]", " ", s).split() if t not in LEGAL_SUFFIXES]
    return " ".join(toks)


def match_first_name(f1: str | None, f2: str | None) -> bool:
    if not f1 or not f2:
        return False
    f1_clean = norm_name(f1)
    f2_clean = norm_name(f2)
    if not f1_clean or not f2_clean:
        return False
    if f1_clean == f2_clean:
        return True
    if f2_clean in NICKNAMES.get(f1_clean, set()) or f1_clean in NICKNAMES.get(f2_clean, set()):
        return True
    return fuzz.ratio(f1_clean, f2_clean) >= 80


def match_last_name(l1: str | None, l2: str | None) -> bool:
    if not l1 or not l2:
        return False
    l1_clean = norm_name(l1)
    l2_clean = norm_name(l2)
    if not l1_clean or not l2_clean:
        return False
    if l1_clean == l2_clean:
        return True
    return fuzz.ratio(l1_clean, l2_clean) >= 80


def match_company_name(c1: str | None, c2: str | None) -> bool:
    if not c1 or not c2:
        return False
    n1, n2 = norm_company(c1), norm_company(c2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    return fuzz.token_set_ratio(n1, n2) >= 88


def _get_related_contact_ids(crm: SQLiteCRM, contact: dict) -> list[int]:
    """Find the contact's ID and any linked duplicate records sharing company_id and last_name."""
    cid = contact.get("id")
    ids = {cid} if cid else set()
    co_id = contact.get("company_id")
    last_name = contact.get("last_name")
    if co_id and last_name:
        rows = crm._all(
            "SELECT id FROM contacts WHERE company_id = ? AND lower(trim(last_name)) = ?",
            (co_id, last_name.strip().lower())
        )
        ids.update(r["id"] for r in rows)
    return sorted(ids)


# ---------------- Deduplication Engine ----------------

def dedupe(extraction: dict, crm: SQLiteCRM, llm_fn=None) -> DedupeResult:
    """Evaluate extracted lead fields against CRM to detect duplicates."""
    email = norm_email(extraction.get("email"))
    first_name = extraction.get("first_name")
    last_name = extraction.get("last_name")
    company_name = extraction.get("company_name")

    # 1. Deterministic Exact Email Matching
    if email:
        exact_matches = crm.find_by_email(email)
        if exact_matches:
            c0 = exact_matches[0]
            ids = set(c["id"] for c in exact_matches)
            ids.update(_get_related_contact_ids(crm, c0))
            return DedupeResult(
                decision="existing",
                match_ids=sorted(ids),
                confidence=1.0,
                reasons=["exact_email_match"]
            )

    lead_domain = email.split("@")[1] if email and "@" in email else None

    # Load CRM contacts with their company details for candidate matching
    query = """
        SELECT c.*, co.name AS co_name, co.domain AS co_domain
        FROM contacts c
        LEFT JOIN companies co ON co.id = c.company_id
    """
    contacts = crm._all(query)

    candidates = []
    for c in contacts:
        first_ok = match_first_name(first_name, c.get("first_name"))
        last_ok = match_last_name(last_name, c.get("last_name"))
        co_ok = match_company_name(company_name, c.get("co_name")) or match_company_name(company_name, c.get("company_name"))
        
        c_email = norm_email(c.get("email"))
        c_dom = c.get("co_domain") or (c_email.split("@")[1] if c_email and "@" in c_email else None)
        dom_ok = bool(lead_domain and c_dom and lead_domain == c_dom)

        if (first_ok and last_ok) or (last_ok and (co_ok or dom_ok)) or (first_ok and dom_ok):
            candidates.append({
                "contact": c,
                "first_ok": first_ok,
                "last_ok": last_ok,
                "co_ok": co_ok,
                "dom_ok": dom_ok,
            })

    # 2. Heuristic Rules
    for cand in candidates:
        c = cand["contact"]
        first_ok = cand["first_ok"]
        last_ok = cand["last_ok"]
        co_ok = cand["co_ok"]
        dom_ok = cand["dom_ok"]

        # Case A: Same person (name/nickname match), same company domain -> existing
        if first_ok and last_ok and dom_ok:
            ids = _get_related_contact_ids(crm, c)
            return DedupeResult(
                decision="existing",
                match_ids=ids,
                confidence=0.95,
                reasons=["name_or_nickname_and_company_domain_match"]
            )

        # Case B: Same person + company name, but domain is unseen/different -> needs_review
        if first_ok and last_ok and co_ok and not dom_ok:
            ids = _get_related_contact_ids(crm, c)
            return DedupeResult(
                decision="needs_review",
                match_ids=ids,
                confidence=0.85,
                reasons=["name_and_company_match_with_new_or_missing_email_domain"]
            )

    # 3. Ambiguous Candidate Disambiguation (LLM Fallback if candidate has partial overlap)
    ambiguous = [
        cand for cand in candidates
        if (cand["last_ok"] and cand["co_ok"]) or (cand["first_ok"] and cand["co_ok"])
    ]
    if ambiguous and llm_fn:
        top_cand = ambiguous[0]["contact"]
        prompt = (
            f"Compare this inbound lead with an existing CRM contact:\n\n"
            f"LEAD:\n"
            f"- Name: {first_name} {last_name}\n"
            f"- Email: {email or 'None'}\n"
            f"- Company: {company_name or 'None'}\n\n"
            f"CRM CONTACT (ID: {top_cand['id']}):\n"
            f"- Name: {top_cand.get('first_name')} {top_cand.get('last_name')}\n"
            f"- Email: {top_cand.get('email') or 'None'}\n"
            f"- Company: {top_cand.get('company_name') or top_cand.get('co_name') or 'None'}\n\n"
            f"Determine if this is the SAME person ('existing'), a DIFFERENT person ('new'), "
            f"or UNCERTAIN ('needs_review')."
        )
        try:
            res = llm_fn(
                prompt,
                system="You are a CRM deduplication expert. Compare lead and contact profiles. Return JSON.",
                max_tokens=300,
                schema=LLMDisambiguation,
            )
            parsed = LLMDisambiguation.model_validate(json.loads(res["text"]))
            m_ids = [top_cand["id"]] if parsed.decision != "new" else []
            return DedupeResult(
                decision=parsed.decision,
                match_ids=m_ids,
                confidence=parsed.confidence,
                reasons=[f"llm_disambiguation: {parsed.rationale}"]
            )
        except Exception:
            pass

    # No match found (or name collision where company/domain completely differs) -> new
    return DedupeResult(
        decision="new",
        match_ids=[],
        confidence=0.95,
        reasons=["no_crm_match_found_or_different_company"]
    )
