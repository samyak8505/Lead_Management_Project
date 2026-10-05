"""Eval adapter. Grows as steps are added (dedupe in Step 6, scoring/routing in Steps 7-8).

  python -m evals.run_eval --pipeline leadflow.workflow.pipeline:predict --only extraction --sleep 7
"""
from .extract import extract
from .dedupe import dedupe


def predict(lead: dict, crm=None) -> dict:
    r = extract(lead["raw"], lead["channel"])
    out = {"extraction": r.data, "injection_detected": r.injection_detected,
           "usage": r.usage, "status": r.status}
    if crm is not None:
        d = dedupe(r.data, crm)
        out["dedupe"] = d.to_dict()
    return out
