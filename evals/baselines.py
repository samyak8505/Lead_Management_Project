"""Reference pipelines used to validate the scorer and set the floor your LLM pipeline must beat.

- oracle:         returns the hand labels (must score 100%; if not, the SCORER is broken)
- regex_baseline: no LLM. Regex extraction + keyword rules + exact-email dedupe.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from evals.scoring import norm_email

DEFAULT_DATASET = "evals/dataset.json"


# ---------------- oracle ----------------
def make_oracle(dataset_path: str = DEFAULT_DATASET):
    cases = {c["id"]: c["labels"] for c in json.loads(Path(dataset_path).read_text(encoding="utf-8"))["cases"]}

    def oracle(lead: dict, crm) -> dict:
        L = cases[lead["id"]]
        e = {k: (v[0] if isinstance(v, list) else v) for k, v in L["extraction"].items()}
        return {"extraction": e, "dedupe": dict(L["dedupe"]), "tier": L["tier"], "route": L["route"],
                "injection_detected": L["injection"]}
    return oracle


def oracle(lead: dict, crm) -> dict:
    return make_oracle()(lead, crm)


# ---------------- regex baseline ----------------
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SPAM_RE = re.compile(r"backlink|btc|inheritance|verify your identity|suspended|lorem ipsum|rank #1|ignore all previous|system prompt|api key", re.I)
SUPPORT_RE = re.compile(r"cannot log in|can't log in|refund|charged twice|crash|password reset|bug", re.I)
INJECT_RE = re.compile(r"ignore (all )?(previous|prior)|system note|assistant:|ai assistant|you are now|disregard", re.I)
HIGH_RE = re.compile(r"urgent|today|this week|asap|tomorrow|thursday|friday|within \d+ ?weeks?|next week|this month", re.I)
MED_RE = re.compile(r"quarter|next month|\d+ weeks", re.I)


def _field(raw: str, label: str):
    m = re.search(rf"^{label}:\s*(.+)$", raw, re.M | re.I)
    return m.group(1).strip() if m else None


def regex_baseline(lead: dict, crm) -> dict:
    raw = lead["raw"]
    name = email = None
    if lead["channel"] == "web_form":
        name, email = _field(raw, "Name"), _field(raw, "Email")
    else:
        m = re.search(r'^From:\s*"?([^"<\n]*?)"?\s*<?([\w.+-]+@[\w.-]+)>?\s*$', raw, re.M)
        if m:
            name, email = m.group(1).strip() or None, m.group(2)
    if not email:
        m = EMAIL_RE.search(raw)
        email = m.group(0) if m else None
    first = last = None
    if name:
        name = name.title() if name.isupper() else name
        parts = name.split()
        first = parts[0] if parts else None
        last = parts[-1] if len(parts) > 1 else None
    company, title = _field(raw, "Company"), _field(raw, "Job title")

    low = raw.lower()
    if SPAM_RE.search(raw): intent = "spam"
    elif SUPPORT_RE.search(raw): intent = "support_request"
    elif "demo" in low: intent = "demo_request"
    elif re.search(r"pric|quote|cost", low): intent = "pricing_inquiry"
    elif re.search(r"partner|reseller", low): intent = "partnership"
    else: intent = "general_question"
    urgency = "high" if HIGH_RE.search(raw) else "medium" if MED_RE.search(raw) else "low"
    budget = "explicit" if re.search(r"budget", low) and re.search(r"\$|inr|lakh|approved|euro", low) else "none"

    if intent == "support_request": tier, route = "none", "support"
    elif intent == "spam": tier, route = "none", "discard"
    elif intent in ("demo_request", "pricing_inquiry") and urgency == "high": tier, route = "hot", "sales"
    elif intent in ("demo_request", "pricing_inquiry", "partnership"): tier, route = "warm", "sales"
    else: tier, route = "cold", "nurture"
    if intent == "spam": urgency, budget = "low", "none"

    ids = [c["id"] for c in crm.find_by_email(email)] if email else []
    if ids:
        dedupe = {"decision": "existing", "match_ids": ids}
    elif first and last and company and crm.search_contacts(name=f"{first} {last}", company=company):
        dedupe = {"decision": "needs_review",
                  "match_ids": [c["id"] for c in crm.search_contacts(name=f"{first} {last}", company=company)]}
    else:
        dedupe = {"decision": "new", "match_ids": []}

    return {"extraction": {"first_name": first, "last_name": last, "email": email, "company_name": company,
                           "job_title": title, "intent": intent, "urgency": urgency, "budget_signal": budget},
            "dedupe": dedupe, "tier": tier, "route": route,
            "injection_detected": bool(INJECT_RE.search(raw))}
