from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .base import DEAL_STAGES

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT,
    industry TEXT,
    employee_count INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    first_name TEXT, last_name TEXT,
    email TEXT, phone TEXT, title TEXT,
    company_name TEXT,                 -- free text, often messy
    company_id INTEGER REFERENCES companies(id),  -- often missing
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    name TEXT NOT NULL,
    amount REAL,
    stage TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    kind TEXT NOT NULL,                -- email | call | note | form
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    entity TEXT NOT NULL,
    result_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(lower(trim(email)));
CREATE INDEX IF NOT EXISTS idx_interactions_contact ON interactions(contact_id);
"""

# Whitelists: field names from callers (and LLMs) are never interpolated blindly into SQL.
CONTACT_FIELDS = {"first_name", "last_name", "email", "phone", "title",
                  "company_name", "company_id", "source", "created_at"}
COMPANY_FIELDS = {"name", "domain", "industry", "employee_count", "created_at"}
INTERACTION_KINDS = {"email", "call", "note", "form"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pick(fields: dict, allowed: set[str]) -> dict:
    return {k: v for k, v in fields.items() if k in allowed}


class SQLiteCRM:
    def __init__(self, path: str | Path = "data/crm.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------- internals ----------
    def _audit(self, actor: str, action: str, entity: str, entity_id: int | None, detail: dict) -> None:
        self.conn.execute(
            "INSERT INTO audit_log(ts, actor, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
            (_now(), actor, action, entity, entity_id, json.dumps(detail, default=str)))

    def _replay(self, key: str | None, entity: str) -> int | None:
        if not key:
            return None
        row = self.conn.execute(
            "SELECT result_id FROM idempotency_keys WHERE key=? AND entity=?", (key, entity)).fetchone()
        return row["result_id"] if row else None

    def _remember(self, key: str | None, entity: str, result_id: int) -> None:
        if key:
            self.conn.execute(
                "INSERT INTO idempotency_keys(key, entity, result_id, created_at) VALUES (?,?,?,?)",
                (key, entity, result_id, _now()))

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        r = self.conn.execute(sql, params).fetchone()
        return dict(r) if r else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ---------- reads ----------
    def get_contact(self, contact_id: int) -> dict | None:
        return self._one("SELECT * FROM contacts WHERE id=?", (contact_id,))

    def find_by_email(self, email: str) -> list[dict]:
        if not email:
            return []
        return self._all("SELECT * FROM contacts WHERE lower(trim(email)) = ?",
                         (email.strip().lower(),))

    def search_contacts(self, name: str | None = None, company: str | None = None,
                        limit: int = 20) -> list[dict]:
        where, params = [], []
        for tok in (name or "").lower().split():
            where.append("lower(coalesce(first_name,'') || ' ' || coalesce(last_name,'')) LIKE ?")
            params.append(f"%{tok}%")
        if company:
            where.append("lower(coalesce(company_name,'')) LIKE ?")
            params.append(f"%{company.strip().lower()}%")
        if not where:
            return []
        sql = f"SELECT * FROM contacts WHERE {' AND '.join(where)} LIMIT ?"
        return self._all(sql, (*params, min(limit, 100)))

    def list_interactions(self, contact_id: int, limit: int = 20) -> list[dict]:
        return self._all("SELECT * FROM interactions WHERE contact_id=? ORDER BY created_at DESC LIMIT ?",
                         (contact_id, limit))

    def list_deals(self, contact_id: int) -> list[dict]:
        return self._all("SELECT * FROM deals WHERE contact_id=? ORDER BY created_at DESC", (contact_id,))

    def stats(self) -> dict:
        return {t: self.conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                for t in ("companies", "contacts", "deals", "interactions", "audit_log")}

    # ---------- writes ----------
    def create_company(self, fields: dict, actor: str = "system") -> dict:
        f = _pick(fields, COMPANY_FIELDS)
        if not f.get("name"):
            raise ValueError("company name required")
        f.setdefault("created_at", _now())
        with self.conn:
            cols = ", ".join(f)
            cur = self.conn.execute(f"INSERT INTO companies({cols}) VALUES ({','.join('?' * len(f))})",
                                    tuple(f.values()))
            self._audit(actor, "create", "company", cur.lastrowid, f)
        return self._one("SELECT * FROM companies WHERE id=?", (cur.lastrowid,))

    def create_contact(self, fields: dict, idempotency_key: str | None = None,
                       actor: str = "system") -> dict:
        f = _pick(fields, CONTACT_FIELDS)
        if not f.get("email") and not (f.get("first_name") and f.get("last_name")):
            raise ValueError("need an email, or both first and last name")
        with self.conn:
            existing = self._replay(idempotency_key, "contact")
            if existing:
                c = self.get_contact(existing)
                c["_replayed"] = True
                return c
            now = _now()
            f.setdefault("created_at", now)
            f["updated_at"] = now
            cur = self.conn.execute(
                f"INSERT INTO contacts({', '.join(f)}) VALUES ({','.join('?' * len(f))})",
                tuple(f.values()))
            self._remember(idempotency_key, "contact", cur.lastrowid)
            self._audit(actor, "create", "contact", cur.lastrowid, f)
        return self.get_contact(cur.lastrowid)

    def update_contact(self, contact_id: int, fields: dict, actor: str = "system") -> dict:
        f = _pick(fields, CONTACT_FIELDS - {"created_at"})
        if not f:
            raise ValueError("no valid fields to update")
        if not self.get_contact(contact_id):
            raise KeyError(f"contact {contact_id} not found")
        f["updated_at"] = _now()
        with self.conn:
            sets = ", ".join(f"{k}=?" for k in f)
            self.conn.execute(f"UPDATE contacts SET {sets} WHERE id=?", (*f.values(), contact_id))
            self._audit(actor, "update", "contact", contact_id, f)
        return self.get_contact(contact_id)

    def create_deal(self, contact_id: int, name: str, amount: float | None = None,
                    stage: str = "new", idempotency_key: str | None = None,
                    actor: str = "system") -> dict:
        if stage not in DEAL_STAGES:
            raise ValueError(f"stage must be one of {DEAL_STAGES}")
        if not self.get_contact(contact_id):
            raise KeyError(f"contact {contact_id} not found")
        with self.conn:
            existing = self._replay(idempotency_key, "deal")
            if existing:
                d = self._one("SELECT * FROM deals WHERE id=?", (existing,))
                d["_replayed"] = True
                return d
            now = _now()
            cur = self.conn.execute(
                "INSERT INTO deals(contact_id, name, amount, stage, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (contact_id, name, amount, stage, now, now))
            self._remember(idempotency_key, "deal", cur.lastrowid)
            self._audit(actor, "create", "deal", cur.lastrowid,
                        {"contact_id": contact_id, "name": name, "amount": amount, "stage": stage})
        return self._one("SELECT * FROM deals WHERE id=?", (cur.lastrowid,))

    def update_deal_stage(self, deal_id: int, stage: str, actor: str = "system") -> dict:
        if stage not in DEAL_STAGES:
            raise ValueError(f"stage must be one of {DEAL_STAGES}")
        if not self._one("SELECT id FROM deals WHERE id=?", (deal_id,)):
            raise KeyError(f"deal {deal_id} not found")
        with self.conn:
            self.conn.execute("UPDATE deals SET stage=?, updated_at=? WHERE id=?", (stage, _now(), deal_id))
            self._audit(actor, "update_stage", "deal", deal_id, {"stage": stage})
        return self._one("SELECT * FROM deals WHERE id=?", (deal_id,))

    def log_interaction(self, contact_id: int, kind: str, summary: str,
                        idempotency_key: str | None = None, actor: str = "system",
                        created_at: str | None = None) -> dict:
        if kind not in INTERACTION_KINDS:
            raise ValueError(f"kind must be one of {sorted(INTERACTION_KINDS)}")
        if not self.get_contact(contact_id):
            raise KeyError(f"contact {contact_id} not found")
        with self.conn:
            existing = self._replay(idempotency_key, "interaction")
            if existing:
                i = self._one("SELECT * FROM interactions WHERE id=?", (existing,))
                i["_replayed"] = True
                return i
            cur = self.conn.execute(
                "INSERT INTO interactions(contact_id, kind, summary, created_at) VALUES (?,?,?,?)",
                (contact_id, kind, summary, created_at or _now()))
            self._remember(idempotency_key, "interaction", cur.lastrowid)
            self._audit(actor, "create", "interaction", cur.lastrowid,
                        {"contact_id": contact_id, "kind": kind})
        return self._one("SELECT * FROM interactions WHERE id=?", (cur.lastrowid,))
