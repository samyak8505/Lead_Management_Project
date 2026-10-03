"""Step 0 deliverable: prove the environment works end to end.
Run:  python scripts/hello_llm.py
"""
from leadflow import config
from leadflow.llm import complete

if __name__ == "__main__":
    print(f"Model: {config.MODEL}")
    out = complete("Reply with exactly: LeadFlow is alive.", max_tokens=20)
    print(out["text"])
    print(f"tokens in/out: {out['input_tokens']}/{out['output_tokens']}")
