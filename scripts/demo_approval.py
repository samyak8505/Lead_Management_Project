"""Demo script for Step 10: Human-in-the-loop Approval Gate.

    python scripts/demo_approval.py
"""
from leadflow.crm import SQLiteCRM
from leadflow.workflow.extract import extract
from leadflow.workflow.dedupe import dedupe
from leadflow.workflow.scoring import score_lead
from leadflow.workflow.draft import draft_email
from leadflow.workflow.approval import ApprovalGate

SAMPLE = """From: "Jennifer Oyelaran" <j.oyelaran@vertexbio-demo.com>
Subject: Pricing for 25 seats

Hi can u send pricing for 25 seats? We want to start next month.

Jennifer Oyelaran
Director of Procurement | Vertex Biosciences"""


def main():
    crm = SQLiteCRM("data/crm.db")
    gate = ApprovalGate("data/crm.db")

    print("1. Processing lead through upstream steps...")
    ext = extract(SAMPLE)
    dedupe_res = dedupe(ext.data, crm)
    score_res = score_lead(SAMPLE, ext.data, dedupe_res, crm)
    draft_res = draft_email(SAMPLE, ext.data, dedupe_res, score_res)

    print("2. Submitting to Approval Gate...")
    req = gate.create_request(
        lead_summary=ext.data,
        dedupe_info=dedupe_res.to_dict(),
        score_info=score_res.to_dict(),
        draft_email=draft_res.to_dict(),
        security_flag=ext.injection_detected,
    )
    print(f"   Created Approval Request: [{req.id}] (Status: {req.status})")

    print("\n3. Current Pending Requests in Gate:")
    pending = gate.list_requests(status="pending")
    for p in pending[:3]:
        print(f"   - ID: {p.id} | Contact: {p.lead_summary.get('first_name')} {p.lead_summary.get('last_name')} | Tier: {p.score_info.get('tier')}")

    print("\n4. Simulating Sales Rep Approval...")
    decision = gate.approve(req.id, reviewer="samyak_sales", notes="Approved standard pricing quote")
    print(f"   Decision Status: {decision.status} by {decision.reviewer} at {decision.decided_at}")
    print(f"   Final Subject: {decision.final_subject}")
    print(f"   Notes: {decision.notes}")

    # Verify audit log
    row = crm._one("SELECT * FROM audit_log WHERE entity_id IS NULL ORDER BY id DESC LIMIT 1")
    print(f"\n5. Audit Log Record: Actor={row['actor']} | Action={row['action']} | Timestamp={row['ts']}")


if __name__ == "__main__":
    main()
