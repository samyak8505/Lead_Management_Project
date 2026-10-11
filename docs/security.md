# LeadFlow Security, Threat Models & Guardrails (Step 15)

## Overview & Security Posture

LeadFlow processes inbound untrusted data from public channels (web forms, emails) and interacts with a business-critical system of record (CRM). To prevent data exfiltration, unauthorized mutations, prompt injections, and denial-of-wallet attacks, LeadFlow adheres to a **defense-in-depth, zero-unapproved-writes architecture**.

Autonomous LLM execution is strictly isolated to read-only extraction and bounded email drafting. All database writes, enrichment lookups, scoring, and routing are strictly deterministic and cryptographically gated.

---

## 1. Threat Model & Mitigations Matrix

| Threat ID | Threat Category | Attack Vector / Scenario | LeadFlow Mitigation & Guardrail | Verified In |
|---|---|---|---|---|
| **THREAT-01** | **Prompt Injection (Direct)** | Attacker submits jailbreak strings (`Ignore instructions`, `You are DAN`, `PWNED`) | Multi-pattern regex scanner + structured grounding validation in `extract.py`. Flags `injection_detected=True`. | `test_direct_prompt_injection_flagged` |
| **THREAT-02** | **Prompt Injection (Indirect)** | Attacker embeds instructions inside email signatures or job titles trying to force sales routing | `route.py` inspects `injection_detected` flag. Diverts to quarantine/discard or attaches a mandatory `security_flag` blocking automated processing. | `test_injection_prevents_unrestricted_sales_routing` |
| **THREAT-03** | **Unauthorized CRM Writes** | An LLM or rogue step attempts to update contacts or create deals without human review | Write-back (`writeback.py`) requires a signed `ApprovalDecision` with status `approved` or `edited`. Rejects `pending` or `rejected` with `PermissionError`. | `test_writeback_rejected_decision_blocks_crm_mutation` |
| **THREAT-04** | **Privilege Escalation** | Workflow steps or agent tools invoke write endpoints during analysis phases | `ToolRegistry` enforces `mode="read"` during evaluation and analysis. Write tools (`create_deal`, `update_contact`) are completely uncallable (`error_type="forbidden"`). | `test_read_registry_cannot_access_write_tools` |
| **THREAT-05** | **SQL Injection** | Attacker inputs SQL syntax (`Robert'); DROP TABLE contacts; --`) in form fields | Fully parameterized SQLite queries (`?` placeholders) and strict schema validation through Pydantic models. | `test_sql_injection_in_lead_fields_is_neutralized` |
| **THREAT-06** | **Denial of Wallet / Runaway Cost** | Infinite agent critique loops or adversarial token flooding | Hard cost ceiling enforced per lead ($0.10 max) via `CostLimitExceededError` in `WorkflowState.record_usage()`. | `test_cost_budget_guard` |
| **THREAT-07** | **Recursion / Step Bombs** | Recursive task loops consuming system resources | Hard step ceiling enforced per workflow run (15 max steps) via `StepLimitExceededError` in `orchestrator.py`. | `test_step_budget_guard` |
| **THREAT-08** | **Partial Write Inconsistency** | Network/SMTP crash occurs midway through contact and deal creation | Single-transaction ACID boundary in `writeback.py`. Any failure initiates an immediate atomic rollback. | `test_writeback.py` |
| **THREAT-09** | **Replay & Duplicate Submissions** | Network retries re-trigger duplicate CRM records and double emails | Enforced `idempotency_key` stored in SQLite. Duplicate executions return cached results without re-running actions. | `test_writeback_idempotency` |

---

## 2. Least-Privilege Architecture

```
                    +------------------------------------+
                    | Inbound Untrusted Lead (Email/Form)|
                    +-----------------+------------------+
                                      |
                                      v
                    +------------------------------------+
                    |  Extract (Read-Only Gemini API)    |
                    |  * Injection Scanner               |
                    |  * Anti-Hallucination Grounding    |
                    +-----------------+------------------+
                                      |
                                      v
                    +------------------------------------+
                    |  Dedupe, Score, Route (Local CPU)  |
                    |  * Read-Only ToolRegistry          |
                    |  * Deterministic Logic             |
                    +-----------------+------------------+
                                      |
                                      v
                    +------------------------------------+
                    |  Draft Email (Bounded Critic Loop) |
                    |  * Max 2 Iterations                |
                    |  * 4-Pillar Safety Rubric          |
                    +-----------------+------------------+
                                      |
                                      v
               ================ HARD SECURITY GATE ===============
                    |  Human Approval Gate (approval.py) |
                    |  * Staged in SQLite               |
                    |  * Requires Human Action           |
               ===================================================
                                      |
                              (Signed Decision)
                                      |
                                      v
                    +------------------------------------+
                    |  Safe Write-Back (writeback.py)    |
                    |  * Single Transaction ACID         |
                    |  * Idempotency Guarded             |
                    +------------------------------------+
```

### Key Security Principles:
1. **Isolated Execution**: Gemini model never has write access to the database or external APIs.
2. **Audit Logging**: Every mutation, approval, edit, and rejection is recorded with timestamp and actor identity in `audit_log`.
3. **Fail-Closed Design**: If an unhandled exception or ambiguous state arises, the pipeline halts safely and records error telemetry without modifying CRM records.
