"""Step 2: build a realistic, deliberately MESSY CRM.
Run:  python scripts/seed_crm.py            (rebuilds data/crm.db, deterministic)
Also writes data/seed_truth.json = ground truth of which records are duplicates
(used to score the dedupe step in Step 6)."""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

from leadflow.crm import SQLiteCRM, DEAL_STAGES

SEED = 42
N_PEOPLE, N_COMPANIES, N_DUP_PEOPLE = 330, 100, 75
DB = Path("data/crm.db")
TRUTH = Path("data/seed_truth.json")

NICK = {"Robert": "Bob", "William": "Bill", "Elizabeth": "Liz", "Michael": "Mike",
        "Jennifer": "Jen", "Christopher": "Chris", "Katherine": "Kate", "Richard": "Rick",
        "James": "Jim", "Thomas": "Tom", "Alexander": "Alex", "Jonathan": "Jon"}
TITLES = ["VP Sales", "Head of Marketing", "CTO", "Operations Manager", "Founder",
          "Procurement Lead", "IT Director", "Product Manager", "CEO", "Office Manager"]
INDUSTRIES = ["SaaS", "Retail", "Manufacturing", "Healthcare", "Logistics", "Finance", "Education"]
SUFFIXES = ["Inc.", "Inc", "Incorporated", "LLC", "Ltd", "Corp", ""]
SOURCES = ["web_form", "email", "referral", "event", "cold_outbound"]
NOTES = ["Asked for pricing details", "Requested a demo next week", "Left voicemail",
         "Sent intro deck", "Said budget is frozen until Q3", "Wants integration with Salesforce",
         "Unsubscribed from newsletter", "Met at trade show", "Needs security questionnaire",
         "Comparing us with a competitor"]


def typo(s: str, rng: random.Random) -> str:
    if len(s) < 4:
        return s
    i = rng.randrange(1, len(s) - 1)
    op = rng.choice(["swap", "drop", "dup"])
    if op == "swap":
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    if op == "drop":
        return s[:i] + s[i + 1:]
    return s[:i] + s[i] + s[i:]


def mangle_case(s: str, rng: random.Random) -> str:
    return rng.choice([str.upper, str.lower, str.title, lambda x: x])(s)


def pad(s: str, rng: random.Random) -> str:
    return rng.choice(["", " ", "  "]) + s + rng.choice(["", " ", "\t"])


def phone(rng: random.Random) -> str:
    d = "".join(rng.choice("0123456789") for _ in range(10))
    return rng.choice([f"{d[:3]}-{d[3:6]}-{d[6:]}", f"({d[:3]}) {d[3:6]}-{d[6:]}",
                       f"+1{d}", d, f"{d[:3]}.{d[3:6]}.{d[6:]}"])


def company_variant(base: str, rng: random.Random) -> str:
    return mangle_case(f"{base} {rng.choice(SUFFIXES)}".strip(), rng)


def rand_ts(rng: random.Random, days_back: int = 730) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=rng.randrange(days_back), seconds=rng.randrange(86400))
    return dt.isoformat(timespec="seconds")


def main() -> None:
    rng = random.Random(SEED)
    fake = Faker()
    Faker.seed(SEED)

    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    crm = SQLiteCRM(DB)

    # ---- companies ----
    companies = []
    for _ in range(N_COMPANIES):
        base = fake.company().split(",")[0].replace(" and Sons", "").strip()
        slug = "".join(ch for ch in base.lower() if ch.isalnum())[:14] or "firm"
        domain = f"{slug}.com"
        c = crm.create_company({"name": f"{base} {rng.choice(['Inc.', 'LLC', 'Ltd'])}", "domain": domain,
                                "industry": rng.choice(INDUSTRIES),
                                "employee_count": rng.choice([5, 12, 40, 120, 450, 2000, 10000]),
                                "created_at": rand_ts(rng)}, actor="seed")
        c["base"] = base
        companies.append(c)

    # ---- clean people ----
    def email_for(fn, ln, domain):
        style = rng.choice(["{f}.{l}", "{f}{l}", "{fi}{l}", "{f}_{l}"])
        local = style.format(f=fn.lower(), l=ln.lower(), fi=fn[0].lower())
        return f"{local}@{domain}"

    people, ids = [], []
    for _ in range(N_PEOPLE):
        fn = rng.choice(list(NICK)) if rng.random() < 0.3 else fake.first_name()
        ln = fake.last_name().replace("'", "")
        co = rng.choice(companies)
        rec = {"first_name": fn, "last_name": ln,
               "email": email_for(fn, ln, co["domain"]),
               "phone": phone(rng), "title": rng.choice(TITLES),
               "company_name": co["name"], "company_id": co["id"],
               "source": rng.choice(SOURCES), "created_at": rand_ts(rng)}
        # missing data
        if rng.random() < 0.15: rec["phone"] = None
        if rng.random() < 0.10: rec["title"] = None
        if rng.random() < 0.08: rec["company_name"] = None
        if rng.random() < 0.20: rec["company_id"] = None
        # casing / whitespace noise on a share of records
        if rng.random() < 0.25: rec["first_name"] = mangle_case(rec["first_name"], rng)
        if rng.random() < 0.25: rec["last_name"] = mangle_case(rec["last_name"], rng)
        if rng.random() < 0.15: rec["email"] = pad(rec["email"], rng)
        if rec["company_name"] and rng.random() < 0.40:
            rec["company_name"] = company_variant(co["base"], rng)
        c = crm.create_contact(rec, actor="seed")
        people.append((c, co)); ids.append(c["id"])

    # ---- deliberate duplicates ----
    truth = []
    for c, co in rng.sample(people, N_DUP_PEOPLE):
        kinds = rng.sample(["case_email", "typo_name", "nickname", "old_email", "no_email"],
                           k=1 if rng.random() < 0.8 else 2)
        dup_ids = []
        for kind in kinds:
            d = {k: c[k] for k in ("first_name", "last_name", "email", "phone", "title",
                                   "company_name", "company_id")}
            d["source"] = rng.choice(SOURCES)
            d["created_at"] = rand_ts(rng, 365)
            if kind == "case_email":
                d["email"] = pad(c["email"].upper(), rng)
            elif kind == "typo_name":
                d["last_name"] = typo(c["last_name"].strip(), rng)
                d["phone"] = None
            elif kind == "nickname":
                d["first_name"] = NICK.get(c["first_name"].strip().title(), c["first_name"][:3])
                d["email"] = email_for(d["first_name"], c["last_name"].strip(), co["domain"])
            elif kind == "old_email":
                old = "".join(ch for ch in co["base"].lower() if ch.isalnum())[:8] + "-old.net"
                d["email"] = email_for(c["first_name"].strip(), c["last_name"].strip(), old)
                d["company_name"] = company_variant(co["base"], rng)
                d["company_id"] = None
            elif kind == "no_email":
                d["email"] = None
                d["company_name"] = company_variant(co["base"], rng)
            dup = crm.create_contact(d, actor="seed")
            dup_ids.append({"id": dup["id"], "kind": kind})
        truth.append({"canonical_id": c["id"], "duplicates": dup_ids})

    all_ids = [r["id"] for r in crm.conn.execute("SELECT id FROM contacts")]

    # ---- deals + interactions ----
    for cid in rng.sample(all_ids, 150):
        stage = rng.choice(DEAL_STAGES)
        crm.create_deal(cid, f"{fake.bs().title()} Rollout", amount=rng.choice([2e3, 8e3, 25e3, 90e3, 250e3]),
                        stage=stage, actor="seed")
    for cid in all_ids:
        for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
            crm.log_interaction(cid, rng.choice(["email", "call", "note"]), rng.choice(NOTES),
                                actor="seed", created_at=rand_ts(rng, 365))

    TRUTH.write_text(json.dumps(truth, indent=2))
    print("Seeded:", crm.stats())
    print(f"Duplicate groups (ground truth): {len(truth)} -> {TRUTH}")
    crm.close()


if __name__ == "__main__":
    sys.exit(main())
