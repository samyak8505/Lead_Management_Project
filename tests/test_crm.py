import pytest
from leadflow.crm import SQLiteCRM


@pytest.fixture
def crm(tmp_path):
    c = SQLiteCRM(tmp_path / "t.db")
    yield c
    c.close()


def test_create_and_find_email_case_insensitive(crm):
    c = crm.create_contact({"first_name": "Ana", "last_name": "Ray", "email": "Ana.Ray@Acme.com"})
    assert crm.find_by_email("  ana.ray@ACME.com ")[0]["id"] == c["id"]


def test_create_needs_identity(crm):
    with pytest.raises(ValueError):
        crm.create_contact({"title": "CEO"})


def test_idempotency_prevents_duplicate(crm):
    a = crm.create_contact({"email": "x@y.com"}, idempotency_key="run-1")
    b = crm.create_contact({"email": "x@y.com"}, idempotency_key="run-1")
    assert a["id"] == b["id"] and b.get("_replayed")
    assert crm.stats()["contacts"] == 1


def test_unknown_fields_ignored_not_injected(crm):
    c = crm.create_contact({"email": "a@b.com", "id; DROP TABLE contacts": "x"})
    assert c["email"] == "a@b.com" and crm.stats()["contacts"] == 1


def test_writes_are_audited(crm):
    c = crm.create_contact({"email": "a@b.com"}, actor="tester")
    crm.update_contact(c["id"], {"title": "CTO"}, actor="tester")
    rows = crm.conn.execute("SELECT action, actor FROM audit_log ORDER BY id").fetchall()
    assert [(r["action"], r["actor"]) for r in rows] == [("create", "tester"), ("update", "tester")]


def test_deal_stage_validation(crm):
    c = crm.create_contact({"email": "a@b.com"})
    d = crm.create_deal(c["id"], "Big deal", 1000)
    with pytest.raises(ValueError):
        crm.update_deal_stage(d["id"], "banana")
    assert crm.update_deal_stage(d["id"], "won")["stage"] == "won"


def test_search_by_name_and_company(crm):
    crm.create_contact({"first_name": "Bob", "last_name": "Stone", "company_name": "ACME Inc"})
    assert len(crm.search_contacts(name="stone", company="acme")) == 1
    assert crm.search_contacts() == []


def test_reject_duplicate_email_flag(crm):
    from leadflow.crm import DuplicateContactError
    crm.create_contact({"email": "dup@x.com"})
    with pytest.raises(DuplicateContactError):
        crm.create_contact({"email": " DUP@x.com "}, reject_duplicate_email=True)
    # default behaviour unchanged (seed relies on this)
    crm.create_contact({"email": "dup@x.com"})
    assert crm.stats()["contacts"] == 2
