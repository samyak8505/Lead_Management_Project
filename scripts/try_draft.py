"""Demo script for Step 9: Email Drafting with Critic loop.

    python scripts/try_draft.py
"""
import json
from leadflow.crm import SQLiteCRM
from leadflow.workflow.extract import extract
from leadflow.workflow.dedupe import dedupe
from leadflow.workflow.scoring import score_lead
from leadflow.workflow.draft import draft_email

SAMPLE = """From: "Anil Kapoor" <anil.kapoor@harborviewhotel-demo.com>
Subject: Channel manager enquiry

Hello,

We manage 4 properties and are looking for channel manager software. Can you share pricing for 4 properties? We need to decide within 3 weeks.

Anil Kapoor
General Manager, Harborview Hotel"""


def main():
    print("=== Processing Inbound Lead ===")
    crm = SQLiteCRM("data/crm.db")
    
    # 1. Extraction
    print("1. Extracting structured data...")
    ext_res = extract(SAMPLE)
    print("   Intent:", ext_res.data.get("intent"), "| Urgency:", ext_res.data.get("urgency"))

    # 2. Dedupe
    print("2. Checking duplicates in CRM...")
    dedupe_res = dedupe(ext_res.data, crm)
    print("   Decision:", dedupe_res.decision, "| Matches:", dedupe_res.match_ids)

    # 3. Scoring
    print("3. Scoring & tiering...")
    score_res = score_lead(SAMPLE, ext_res.data, dedupe_res=dedupe_res, crm=crm)
    print(f"   Score: {score_res.score}/100 ({score_res.tier.upper()}) | Rationale: {score_res.rationale}")

    # 4. Drafting with Critic Loop
    print("4. Running Generator-Critic Drafting Loop (max 2 iterations)...")
    draft_res = draft_email(SAMPLE, ext_res.data, dedupe_res=dedupe_res, score_res=score_res)
    print(f"   Status: {draft_res.status} | Loops: {draft_res.loops} | Critic Score: {draft_res.critic_score}/5")
    print(f"   Subject: {draft_res.subject}")
    print("\n--- Proposed Email Body ---\n" + draft_res.body + "\n---------------------------")


if __name__ == "__main__":
    main()
