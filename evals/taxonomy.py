"""Label vocabulary + consistency rules. See evals/labels.md for the human-readable annotation guide."""
INTENTS = ("demo_request", "pricing_inquiry", "partnership", "general_question",
           "support_request", "vendor_pitch", "spam", "unclear")
URGENCY = ("high", "medium", "low")
BUDGET = ("explicit", "implied", "none")
TIERS = ("hot", "warm", "cold", "none")
ROUTES = ("sales", "nurture", "support", "discard")
DEDUPE = ("new", "existing", "needs_review")

TEXT_FIELDS = ("first_name", "last_name", "email", "company_name", "job_title")
ENUM_FIELDS = ("intent", "urgency", "budget_signal")
EXTRACTION_FIELDS = TEXT_FIELDS + ENUM_FIELDS

NOT_A_SALES_LEAD = {"spam", "vendor_pitch", "unclear"}


def check_labels(labels: dict) -> list[str]:
    """Return a list of problems (empty = consistent). Catches labeling mistakes."""
    errs: list[str] = []
    e = labels.get("extraction", {})
    for f in EXTRACTION_FIELDS:
        if f not in e:
            errs.append(f"extraction missing '{f}'")
    if e.get("intent") not in INTENTS: errs.append(f"bad intent {e.get('intent')!r}")
    if e.get("urgency") not in URGENCY: errs.append(f"bad urgency {e.get('urgency')!r}")
    if e.get("budget_signal") not in BUDGET: errs.append(f"bad budget_signal {e.get('budget_signal')!r}")
    tier, route = labels.get("tier"), labels.get("route")
    if tier not in TIERS: errs.append(f"bad tier {tier!r}")
    if route not in ROUTES: errs.append(f"bad route {route!r}")
    d = labels.get("dedupe", {})
    if d.get("decision") not in DEDUPE: errs.append(f"bad dedupe decision {d.get('decision')!r}")
    if d.get("decision") == "new" and d.get("match_ids"):
        errs.append("dedupe 'new' must have no match_ids")
    if d.get("decision") in ("existing", "needs_review") and not d.get("match_ids"):
        errs.append(f"dedupe '{d.get('decision')}' needs match_ids")
    intent = e.get("intent")
    if intent == "support_request" and (tier, route) != ("none", "support"):
        errs.append("support_request must be tier=none, route=support")
    elif intent in NOT_A_SALES_LEAD and (tier, route) != ("none", "discard"):
        errs.append(f"{intent} must be tier=none, route=discard")
    elif intent in ("demo_request", "pricing_inquiry", "partnership", "general_question"):
        if tier in ("hot", "warm") and route != "sales":
            errs.append("hot/warm must route to sales")
        if tier == "cold" and route != "nurture":
            errs.append("cold must route to nurture")
        if tier == "none":
            errs.append("a genuine sales intent cannot have tier=none")
    if intent in NOT_A_SALES_LEAD | {"support_request"} and (e.get("urgency") != "low" and intent != "support_request"):
        errs.append("urgency must be 'low' for spam/vendor_pitch/unclear")
    return errs
