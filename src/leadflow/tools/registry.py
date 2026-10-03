"""ToolRegistry: the single door between an LLM (or workflow step) and the CRM.

- validates every call against its schema
- converts every failure into a structured ToolResult the model can read and fix (never raises)
- `mode="read"` exposes only read tools; write tools are invisible AND uncallable
- the audit `actor` is fixed at construction, so the model can never spoof it
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from leadflow.crm import CRMClient, DuplicateContactError
from . import crm_tools as t
from . import schemas as s

log = logging.getLogger("leadflow.tools")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[CRMClient, str, Any], dict]
    writes: bool


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error_type: str | None = None   # validation | not_found | conflict | invalid | forbidden | unknown_tool | internal
    error: str | None = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "data": self.data, "error_type": self.error_type, "error": self.error}


ALL_TOOLS: tuple[Tool, ...] = (
    Tool("search_contact",
         "Find CRM contacts by exact email, or by name and/or company. Use before creating anything to avoid duplicates.",
         s.SearchContactInput, t.search_contact, writes=False),
    Tool("get_contact_details",
         "Get one contact with their deals and the 5 most recent interactions.",
         s.GetContactDetailsInput, t.get_contact_details, writes=False),
    Tool("create_contact",
         "Create a new contact. Rejected if the exact email already exists. Requires an idempotency_key.",
         s.CreateContactInput, t.create_contact, writes=True),
    Tool("update_contact",
         "Update fields on an existing contact. Only provided fields change.",
         s.UpdateContactInput, t.update_contact, writes=True),
    Tool("create_deal",
         "Create a deal for a contact. Requires an idempotency_key.",
         s.CreateDealInput, t.create_deal, writes=True),
    Tool("update_deal_stage",
         "Move a deal to a new stage (new, qualified, proposal, won, lost).",
         s.UpdateDealStageInput, t.update_deal_stage, writes=True),
    Tool("log_interaction",
         "Record an email, call, note or form submission on a contact. Requires an idempotency_key.",
         s.LogInteractionInput, t.log_interaction, writes=True),
)


def _fmt_validation(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "input"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


class ToolRegistry:
    def __init__(self, crm: CRMClient, actor: str, mode: Literal["read", "readwrite"]):
        self._crm, self._actor, self._mode = crm, actor, mode
        self._all = {x.name: x for x in ALL_TOOLS}
        self._allowed = {x.name for x in ALL_TOOLS if mode == "readwrite" or not x.writes}

    def names(self) -> list[str]:
        return sorted(self._allowed)

    def specs(self) -> list[dict]:
        """Tool descriptions + JSON Schemas to hand to an LLM / MCP server."""
        return [{"name": x.name, "description": x.description,
                 "parameters": x.input_model.model_json_schema(), "writes": x.writes}
                for n, x in self._all.items() if n in self._allowed]

    def call(self, name: str, args: dict | None) -> ToolResult:
        tool = self._all.get(name)
        if tool is None:
            return ToolResult(False, error_type="unknown_tool", error=f"no tool named '{name}'")
        if name not in self._allowed:
            return ToolResult(False, error_type="forbidden", error=f"'{name}' is not permitted in {self._mode} mode")
        if not isinstance(args, dict):
            return ToolResult(False, error_type="validation", error="arguments must be an object")
        try:
            inp = tool.input_model.model_validate(args)
            return ToolResult(True, data=tool.handler(self._crm, self._actor, inp))
        except ValidationError as e:
            return ToolResult(False, error_type="validation", error=_fmt_validation(e))
        except DuplicateContactError as e:
            return ToolResult(False, data={"existing_ids": e.existing_ids},
                              error_type="conflict", error=str(e))
        except KeyError as e:
            return ToolResult(False, error_type="not_found", error=str(e.args[0]) if e.args else "not found")
        except ValueError as e:
            return ToolResult(False, error_type="invalid", error=str(e))
        except Exception:  # never leak internals to the model
            log.exception("tool %s crashed", name)
            return ToolResult(False, error_type="internal", error="internal error; see logs")


def build_registry(crm: CRMClient, actor: str = "leadflow-agent",
                   mode: Literal["read", "readwrite"] = "read") -> ToolRegistry:
    """Default is READ-ONLY. Opt in to writes explicitly (only the write-back step should)."""
    return ToolRegistry(crm, actor, mode)
