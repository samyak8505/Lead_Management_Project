# LeadFlow

Agentic lead-intake workflow: extract, dedupe, score, route, draft, human approval, CRM write-back.
See `docs/architecture.md` for the design.

## Quickstart
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt && pip install -e .
cp .env.example .env                                  # add your Gemini API key (free: aistudio.google.com/apikey)
python scripts/hello_llm.py
pytest
```

## Status
- [x] Step 0: setup
- [x] Step 1: architecture
- [ ] Step 2: CRM layer
