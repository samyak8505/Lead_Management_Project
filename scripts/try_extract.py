"""Run ONE extraction against the live LLM (1 API call). Do this before the full eval.
    python scripts/try_extract.py
    python scripts/try_extract.py --channel web_form "Name: Ana Ray\nEmail: ana@acme.com\nMessage: pricing please"
"""
import argparse
import json

from leadflow.workflow.extract import extract

SAMPLE = """From: "JENNIFER OYELARAN" <j.oyelaran@vertexbio-demo.com>
Subject: RE: RE: FWD pricing??

hi can u send pricing for 25 seats. we want to start sometime next quarter.

Jennifer Oyelaran
Director of Procurement | Vertex Biosciences
Sent from my iPhone"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default=SAMPLE)
    ap.add_argument("--channel", default="email")
    a = ap.parse_args()
    print(json.dumps(extract(a.text.replace("\\n", "\n"), a.channel).to_dict(), indent=2, ensure_ascii=False))
