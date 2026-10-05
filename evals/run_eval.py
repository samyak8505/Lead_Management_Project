"""Score any pipeline against the labeled dataset.

  python -m evals.run_eval --pipeline evals.baselines:regex_baseline
  python -m evals.run_eval --pipeline leadflow.workflow.pipeline:predict --only extraction --sleep 4
  python -m evals.run_eval --pipeline evals.baselines:oracle --check

A pipeline is a function  predict(lead, crm) -> dict  (format: evals/scoring.py).
`lead` has ONLY id / channel / raw (labels are never shown). `crm` is a throwaway COPY of data/crm.db,
so evals can never modify your real CRM."""
from __future__ import annotations
import argparse
import importlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from leadflow.crm import SQLiteCRM
from evals.build_dataset import crm_fingerprint
from evals.scoring import aggregate, format_report

DEFAULT_DATASET = "evals/dataset.json"
DEFAULT_DB = "data/crm.db"


def load_pipeline(spec: str):
    mod, fn = spec.split(":")
    return getattr(importlib.import_module(mod), fn)


def run(pipeline, dataset_path=DEFAULT_DATASET, db_path=DEFAULT_DB, only=None, limit=None,
        sleep: float = 0.0, tags=None, name: str = "") -> dict:
    ds = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    cases = ds["cases"]
    excluded, reason = [], ""
    if ds["meta"].get("crm_fingerprint") != crm_fingerprint(db_path):
        excluded = [c["id"] for c in cases if any(t.startswith("crm_") for t in c["tags"])]
        cases = [c for c in cases if c["id"] not in excluded]
        reason = "dataset was built from a different CRM (re-run: python -m evals.build_dataset)"
    if tags:
        cases = [c for c in cases if set(c["tags"]) & set(tags)]
    if limit:
        cases = cases[:limit]

    from evals.scoring import score_case
    tmp = Path(tempfile.mkdtemp()) / "crm_copy.db"
    shutil.copy(db_path, tmp)
    crm = SQLiteCRM(tmp)
    results = []
    try:
        for i, c in enumerate(cases):
            public = {"id": c["id"], "channel": c["channel"], "raw": c["raw"]}
            try:
                pred = pipeline(public, crm)
            except Exception as e:  # a crash is a failure, not a reason to stop the eval
                print(f"  ! {c['id']} crashed: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
                pred = None
            results.append(score_case(c, pred, set(only) if only else None))
            if sleep and i < len(cases) - 1:
                time.sleep(sleep)
    finally:
        crm.close()
    return aggregate(results, {"pipeline": name, "dataset": dataset_path, "excluded": excluded, "excluded_reason": reason})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pipeline", required=True, help="module:function")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--only", help="comma list of: extraction,dedupe,tier,route")
    ap.add_argument("--tags", help="comma list, e.g. injection,spam")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between cases (free-tier rate limits)")
    ap.add_argument("--save", metavar="NAME", help="write evals/results/NAME.json")
    ap.add_argument("--check", action="store_true", help="exit 1 if a target is missed")
    a = ap.parse_args()

    if not Path(a.db).exists():
        sys.exit("data/crm.db not found. Run: python scripts/seed_crm.py")
    if not Path(a.dataset).exists():
        sys.exit("evals/dataset.json not found. Run: python -m evals.build_dataset")

    rep = run(load_pipeline(a.pipeline), a.dataset, a.db,
              only=a.only.split(",") if a.only else None,
              limit=a.limit, sleep=a.sleep, tags=a.tags.split(",") if a.tags else None, name=a.pipeline)
    print(format_report(rep))
    if a.save:
        out = Path("evals/results") / f"{a.save}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(rep, indent=2, default=str))
        print(f"\nsaved -> {out}")
    if a.check and not all(v["met"] for v in rep["targets"].values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
