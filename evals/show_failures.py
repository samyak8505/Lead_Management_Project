"""Error analysis: which fields/decisions were wrong, label vs prediction.

  python -m evals.show_failures evals/results/extract_v2.json
  python -m evals.show_failures evals/results/extract_v2.json --raw     # also print each failing input
"""
import argparse
import json
from pathlib import Path

from evals.scoring import format_failures

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--dataset", default="evals/dataset.json")
    a = ap.parse_args()
    rep = json.loads(Path(a.results).read_text(encoding="utf-8"))
    cases = {}
    if Path(a.dataset).exists():
        cases = {c["id"]: c for c in json.loads(Path(a.dataset).read_text(encoding="utf-8"))["cases"]}
    print(format_failures(rep, cases, a.raw))
