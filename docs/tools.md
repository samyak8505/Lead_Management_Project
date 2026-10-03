# Tools layer (Step 3)

The only way an LLM or workflow step touches the CRM is through `ToolRegistry.call(name, args)`.

| Tool | Type | Notes |
|---|---|---|
| search_contact | read | email (exact, case-insensitive) or name/company |
| get_contact_details | read | contact + deals + 5 recent interactions |
| create_contact | write | idempotency key required; rejects exact-email duplicates |
| update_contact | write | only provided fields change |
| create_deal | write | idempotency key required |
| update_deal_stage | write | stage enum validated |
| log_interaction | write | idempotency key required |

## Safety properties (each is tested)
1. **Read-only by default.** `build_registry(crm)` hides and blocks every write tool.
2. **No delete/drop tools exist.**
3. **Strict schemas.** Unknown fields rejected, so the model cannot pass `actor`, `id`, or SQL fragments.
4. **Actor is fixed at construction**, so the audit log can't be spoofed by model output.
5. **Idempotent writes.** A retry with the same key returns the original record (`replayed: true`).
6. **Structured errors, never exceptions.** `validation | not_found | conflict | invalid | forbidden | unknown_tool | internal`, readable by the model so it can self-correct.
7. **Input is untrusted data.** Control characters stripped, lengths bounded; injection text is stored inertly.

Exact-email dedupe is a hard guard here. Fuzzy and ambiguous matches are Step 6.
