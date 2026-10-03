"""Input schemas for every tool. These are the contract between the LLM and the CRM:
strict (unknown fields rejected), bounded (max lengths), and sanitized (control chars stripped).
Anything an LLM sends that doesn't fit is rejected with a message it can read and self-correct from."""
from __future__ import annotations
import re
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from leadflow.crm import DEAL_STAGES

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean(v: str) -> str:
    v = _CTRL.sub("", v).strip()
    if not v:
        raise ValueError("must not be empty")
    return v


def _email(v: str) -> str:
    v = _clean(v).lower()
    if len(v) > 254 or not _EMAIL.match(v):
        raise ValueError("not a valid email address")
    return v


def _text(max_len: int):
    return Annotated[str, Field(max_length=max_len), AfterValidator(_clean)]


Email = Annotated[str, AfterValidator(_email)]
Name = _text(100)
Phone = _text(40)
Title = _text(120)
Company = _text(200)
DealName = _text(200)
Summary = _text(2000)
IdemKey = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Source = Literal["web_form", "email", "referral", "event", "cold_outbound"]
Stage = Literal[DEAL_STAGES]  # type: ignore[valid-type]
InteractionKind = Literal["email", "call", "note", "form"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")   # LLM can't smuggle in e.g. "actor" or "id"


# ---------- reads ----------
class SearchContactInput(_In):
    email: Optional[Email] = Field(None, description="Exact email (case-insensitive). Takes priority over name/company.")
    name: Optional[Name] = Field(None, description="Full or partial person name, e.g. 'bob stone'.")
    company: Optional[Company] = Field(None, description="Company name or fragment.")
    limit: int = Field(10, ge=1, le=25, description="Max results.")

    @model_validator(mode="after")
    def _need_one(self):
        if not (self.email or self.name or self.company):
            raise ValueError("provide at least one of: email, name, company")
        return self


class GetContactDetailsInput(_In):
    contact_id: int = Field(gt=0, description="CRM contact id from search_contact.")


# ---------- writes ----------
class CreateContactInput(_In):
    first_name: Optional[Name] = None
    last_name: Optional[Name] = None
    email: Optional[Email] = None
    phone: Optional[Phone] = None
    title: Optional[Title] = None
    company_name: Optional[Company] = None
    source: Optional[Source] = None
    idempotency_key: IdemKey = Field(description="Unique per logical action, e.g. '<run_id>:create_contact'. Retries with the same key never duplicate.")

    @model_validator(mode="after")
    def _identity(self):
        if not self.email and not (self.first_name and self.last_name):
            raise ValueError("provide an email, or both first_name and last_name")
        return self


class UpdateContactInput(_In):
    contact_id: int = Field(gt=0)
    first_name: Optional[Name] = None
    last_name: Optional[Name] = None
    email: Optional[Email] = None
    phone: Optional[Phone] = None
    title: Optional[Title] = None
    company_name: Optional[Company] = None

    @model_validator(mode="after")
    def _something(self):
        if not any(v is not None for k, v in self.model_dump().items() if k != "contact_id"):
            raise ValueError("provide at least one field to update")
        return self


class CreateDealInput(_In):
    contact_id: int = Field(gt=0)
    name: DealName
    amount: Optional[float] = Field(None, ge=0, le=1_000_000_000)
    stage: Stage = "new"
    idempotency_key: IdemKey


class UpdateDealStageInput(_In):
    deal_id: int = Field(gt=0)
    stage: Stage


class LogInteractionInput(_In):
    contact_id: int = Field(gt=0)
    kind: InteractionKind
    summary: Summary
    idempotency_key: IdemKey
