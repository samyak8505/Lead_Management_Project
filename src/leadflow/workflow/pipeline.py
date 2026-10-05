"""Eval adapter. Grows as steps are added (dedupe in Step 6, scoring/routing in Steps 7-8).

  python -m evals.run_eval --pipeline leadflow.workflow.pipeline:predict --only extraction --sleep 7
"""
from .extract import extract
from .dedupe import dedupe
from .scoring import score_lead
from .route import route_lead


def predict(lead: dict, crm=None) -> dict:
    r = extract(lead["raw"], lead["channel"])
    out = {"extraction": r.data, "injection_detected": r.injection_detected,
           "usage": r.usage, "status": r.status}
    dedupe_res = None
    if crm is not None:
        dedupe_res = dedupe(r.data, crm)
        out["dedupe"] = dedupe_res.to_dict()

    s = score_lead(lead["raw"], r.data, dedupe_res=dedupe_res, crm=crm)
    out["score"] = s.score
    out["tier"] = s.tier
    out["scoring_reasons"] = s.reasons
    out["scoring_rationale"] = s.rationale

    rt = route_lead(lead["raw"], r.data, s, injection_detected=r.injection_detected)
    out["route"] = rt.route
    out["route_confidence"] = rt.confidence
    return out
