"""Step 3 demo: call the tools exactly the way an LLM/workflow will.
Run:  python scripts/seed_crm.py   (once)   then   python scripts/demo_tools.py"""
import json
from leadflow.crm import SQLiteCRM
from leadflow.tools import build_registry

crm = SQLiteCRM("data/crm.db")
ro = build_registry(crm)                                   # read-only by default
rw = build_registry(crm, actor="demo", mode="readwrite")


def show(title, res):
    print(f"\n--- {title}\n{json.dumps(res.to_dict(), indent=2, default=str)[:700]}")


print("Read-only tools:", ro.names())
first = json.load(open("data/seed_truth.json"))[0]["canonical_id"]
c = crm.get_contact(first)
show("search by email (case-insensitive)", ro.call("search_contact", {"email": (c["email"] or "").upper()}))
show("search by name", ro.call("search_contact", {"name": c["last_name"]}))
show("details", ro.call("get_contact_details", {"contact_id": first}))
show("write attempt in read mode", ro.call("create_contact", {"email": "a@b.com", "idempotency_key": "demo-key-0001"}))
show("bad input (LLM mistake)", rw.call("create_deal", {"contact_id": first, "name": "X", "stage": "banana", "idempotency_key": "k"}))
show("duplicate email blocked", rw.call("create_contact", {"email": c["email"], "idempotency_key": "demo-key-0002"}))
show("create (new)", rw.call("create_contact", {"first_name": "Demo", "last_name": "Person", "email": "demo.person@example.com", "idempotency_key": "demo-key-0003"}))
show("same call retried", rw.call("create_contact", {"first_name": "Demo", "last_name": "Person", "email": "demo.person@example.com", "idempotency_key": "demo-key-0003"}))
