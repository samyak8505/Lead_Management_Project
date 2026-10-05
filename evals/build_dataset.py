"""Builds evals/dataset.json = 35 static cases + 15 CRM-linked cases.

The linked cases (duplicates of people who really exist in YOUR seeded CRM) are generated from
data/crm.db + data/seed_truth.json, so their dedupe labels are exact by construction.

    python scripts/seed_crm.py
    python -m evals.build_dataset
"""
from __future__ import annotations
import hashlib
import json
import random
import re
import sqlite3
from datetime import date
from pathlib import Path

from leadflow.crm.sqlite_adapter import EMAIL_NORM_SQL
from evals.cases_static import STATIC_CASES, case, ext, form, mail
from evals.taxonomy import check_labels

NICK = {"Robert": "Bob", "William": "Bill", "Elizabeth": "Liz", "Michael": "Mike",
        "Jennifer": "Jen", "Christopher": "Chris", "Katherine": "Kate", "Richard": "Rick",
        "James": "Jim", "Thomas": "Tom", "Alexander": "Alex", "Jonathan": "Jon"}
DM_TITLES = ["VP Operations", "Head of Procurement", "Chief Technology Officer", "Founder", "Managing Director"]
FICTIONAL_COMPANIES = [("Kestrel Robotics", "kestrelrobotics-demo.io", "Product Manager"),
                       ("Nimbus Dental Group", "nimbusdental-demo.com", "Practice Owner")]

TEMPLATES = {
    "hot": dict(body="Hi,\n\nWe would like a demo of your platform. We have an approved budget of $25,000 and want to get started within the next 3 weeks.\n",
                dm=True, intent="demo_request", urgency="high", budget="explicit", tier="hot", route="sales"),
    "warm": dict(body="Hello,\n\nCould you send us your pricing? We are just looking into options for our team, no particular timeline.\n",
                 dm=False, intent="pricing_inquiry", urgency="low", budget="none", tier="warm", route="sales"),
    "cold": dict(body="Hi,\n\nWe are exploring tools in this space. Is there any documentation I can read?\n",
                 dm=False, intent="general_question", urgency="low", budget="none", tier="cold", route="nurture"),
}


def norm_email(e: str | None) -> str:
    return (e or "").strip().lower()


def crm_fingerprint(db_path: str | Path) -> str:
    db = sqlite3.connect(str(db_path))
    emails = sorted(norm_email(r[0]) for r in db.execute("SELECT email FROM contacts") if r[0])
    db.close()
    return hashlib.sha1("\n".join(emails).encode()).hexdigest()[:16]


def _cased(s: str | None) -> bool:
    s = (s or "").strip()
    return bool(s) and s == s.title()


def build(db_path="data/crm.db", truth_path="data/seed_truth.json",
          out_path="evals/dataset.json", seed: int = 7) -> dict:
    rng = random.Random(seed)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    contacts = [dict(r) for r in db.execute(
        "SELECT c.*, co.name AS co_name FROM contacts c LEFT JOIN companies co ON co.id = c.company_id ORDER BY c.id")]
    truth = json.loads(Path(truth_path).read_text())
    group = {g["canonical_id"]: {g["canonical_id"], *[d["id"] for d in g["duplicates"]]} for g in truth}
    dup_members = {d["id"] for g in truth for d in g["duplicates"]}
    all_emails = {norm_email(c["email"]) for c in contacts if c["email"]}
    all_domains = {e.split("@")[1] for e in all_emails}

    eligible = [c for c in contacts
                if c["id"] not in dup_members and c["email"] and c["co_name"]
                and _cased(c["first_name"]) and _cased(c["last_name"])]

    def related(c: dict) -> list[int]:
        last = c["last_name"].strip().lower()
        ids = {x["id"] for x in contacts
               if x["company_id"] == c["company_id"] and (x["last_name"] or "").strip().lower() == last}
        return sorted(ids | group.get(c["id"], {c["id"]}))

    used: set[int] = set()

    def pick(pool: list[dict], n: int) -> list[dict]:
        pool = [c for c in pool if c["id"] not in used]
        chosen = rng.sample(pool, n)
        used.update(c["id"] for c in chosen)
        return chosen

    def lead(kind, first, last, email_raw, company, subject):
        t = TEMPLATES[kind]
        title = rng.choice(DM_TITLES) if t["dm"] else None
        sig = f"{first} {last}\n" + (f"{title}, {company}" if title else company)
        raw = mail(f"{first} {last}", email_raw, subject, t["body"] + "\n" + sig)
        e = ext(first, last, norm_email(email_raw), company, title,
                intent=t["intent"], urgency=t["urgency"], budget=t["budget"])
        return raw, e, t["tier"], t["route"]

    def linked(tags, raw, e, tier, route, decision, ids, channel="email"):
        c = case(tags, channel, raw, e, tier, route)
        c["labels"]["dedupe"] = {"decision": decision, "match_ids": sorted(ids)}
        return c

    out = []

    # A) 7 exact-email matches with case variation
    variants = [str.lower, str.upper, lambda s: s[0].upper() + s[1:].lower(), str.lower, str.upper,
                str.lower, lambda s: s[0].upper() + s[1:].lower()]
    kinds = ["hot", "warm", "warm", "cold", "hot", "warm", "cold"]
    for c, v, k in zip(pick(eligible, 7), variants, kinds):
        email_raw = v(norm_email(c["email"]))
        ids = [r["id"] for r in db.execute(f"SELECT id FROM contacts WHERE {EMAIL_NORM_SQL} = ?", (norm_email(c["email"]),))]
        raw, e, tier, route = lead(k, c["first_name"].strip(), c["last_name"].strip(), email_raw, c["co_name"], "Inquiry")
        out.append(linked(["crm_exact"], raw, e, tier, route, "existing", ids))

    # B) 3 nickname + new email, same company domain -> existing (fuzzy)
    for c, k in zip(pick([x for x in eligible if x["first_name"].strip() in NICK], 3), ["warm", "hot", "warm"]):
        first, last = NICK[c["first_name"].strip()], c["last_name"].strip()
        domain = norm_email(c["email"]).split("@")[1]
        for pat in ("{f}.{l}", "{f}{l}", "{l}.{f}", "{f}_{l}"):
            email_raw = pat.format(f=first.lower(), l=last.lower()) + "@" + domain
            if email_raw not in all_emails:
                break
        raw, e, tier, route = lead(k, first, last, email_raw, c["co_name"], "Question about your product")
        out.append(linked(["crm_fuzzy"], raw, e, tier, route, "existing", related(c)))

    # C) 2 same person + company, but a never-seen email domain -> needs_review
    for c, k in zip(pick(eligible, 2), ["warm", "hot"]):
        slug = re.sub(r"[^a-z0-9]", "", c["co_name"].lower())[:10]
        domain = f"{slug}-mail.net"
        assert domain not in all_domains
        raw, e, tier, route = lead(k, c["first_name"].strip(), c["last_name"].strip(), f"{c['first_name'].strip().lower()}@{domain}",
                                   c["co_name"], "Following up")
        out.append(linked(["crm_domainchange"], raw, e, tier, route, "needs_review", related(c)))

    # D) 2 name collisions: same name, different company + domain -> new
    for c, (co, dom, title), k in zip(pick(eligible, 2), FICTIONAL_COMPANIES, ["warm", "cold"]):
        first, last = c["first_name"].strip(), c["last_name"].strip()
        t = TEMPLATES[k]
        raw = mail(f"{first} {last}", f"{first.lower()}.{last.lower()}@{dom}", "Hello",
                   t["body"] + f"\n{first} {last}\n{title}, {co}")
        e = ext(first, last, f"{first.lower()}.{last.lower()}@{dom}", co, title,
                intent=t["intent"], urgency=t["urgency"], budget=t["budget"])
        out.append(linked(["crm_namecollision"], raw, e, t["tier"], t["route"], "new", []))

    # E) 1 web form with no email: name + company match an existing contact -> needs_review
    for c in pick([x for x in eligible if len(related(x)) == 1], 1):
        first, last = c["first_name"].strip(), c["last_name"].strip()
        raw = form(f"{first} {last}", None, c["co_name"], phone="+91 90000 00001", message="Please call me about your product.")
        e = ext(first, last, None, c["co_name"], None, intent="general_question")
        out.append(linked(["crm_noemail"], raw, e, "warm", "sales", "needs_review", related(c), channel="web_form"))

    cases = [dict(c) for c in STATIC_CASES] + out
    for i, c in enumerate(cases, 1):
        c["id"] = f"L{i:03d}"
    problems = {c["id"]: check_labels(c["labels"]) for c in cases if check_labels(c["labels"])}
    assert not problems, problems

    ds = {"meta": {"version": 1, "built": date.today().isoformat(), "n_cases": len(cases),
                   "n_static": len(STATIC_CASES), "n_linked": len(out),
                   "crm_fingerprint": crm_fingerprint(db_path)},
          "cases": cases}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(ds, indent=2, ensure_ascii=False), encoding="utf-8")
    db.close()
    return ds


if __name__ == "__main__":
    ds = build()
    tags: dict[str, int] = {}
    for c in ds["cases"]:
        for t in c["tags"]:
            tags[t] = tags.get(t, 0) + 1
    print(f"Wrote evals/dataset.json: {ds['meta']['n_cases']} cases "
          f"({ds['meta']['n_static']} static + {ds['meta']['n_linked']} CRM-linked)")
    print("Tags:", dict(sorted(tags.items())))
