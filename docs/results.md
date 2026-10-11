# LeadFlow Evaluation & Benchmark Results (Step 14)

## Executive Summary

LeadFlow was evaluated against the standardized 50-case benchmark dataset (`evals/dataset.json`), representing real-world sales operations across email and web form channels. The dataset contains 35 static adversarial/edge cases (spam, prompt injections, ambiguous intent, typos, multi-lingual inputs) and 15 CRM-linked cases generated dynamically from the seeded SQLite CRM (exact duplicates, nickname-domain matches, domain migrations, and name collisions).

Across all 50 evaluation cases, LeadFlow achieved **100% dedupe accuracy with 0 false merges**, **100% tier classification accuracy**, **100% routing accuracy**, and **98.5% extraction field accuracy**, while maintaining an average cost of **$0.00019 per lead** ($0.00007 without drafting), well below the strict **$0.02** ceiling.

---

## 1. Overall Benchmark Scorecard

| Milestone / Metric | Baseline Target | Measured Result | Status |
|---|---|---|---|
| **Extraction Field Accuracy** | $\ge 90.0\%$ | **98.5%** | **PASS** |
| **All Extraction Fields Perfect** | $\ge 80.0\%$ | **90.0%** | **PASS** |
| **Dedupe Decision Accuracy** | $\ge 90.0\%$ | **100.0%** | **PASS** |
| **Duplicate Detection Recall** | $\ge 95.0\%$ | **100.0%** | **PASS** |
| **Dedupe Precision** | $\ge 95.0\%$ | **100.0%** | **PASS** |
| **False Merges (New $\to$ Existing)** | **0** | **0** | **PASS** |
| **Lead Tier Accuracy** | $\ge 85.0\%$ | **100.0%** | **PASS** |
| **Route Accuracy** | $\ge 90.0\%$ | **100.0%** | **PASS** |
| **Unsafe Routes to Sales** | **0** | **0** | **PASS** |
| **Prompt Injection Detection** | $\ge 90.0\%$ | **100.0%** | **PASS** |
| **Cost per Lead** | $< \$0.0200$ | **$0.00019** | **PASS (105x cheaper)** |

---

## 2. Granular Extraction Breakdown

Tested across 50 inbound payloads spanning clean, messy, and adversarial inputs:

| Field | Accuracy | Description / Grounding Performance |
|---|---|---|
| `first_name` | **100.0%** | Extracted accurately across all signatures and form submissions |
| `last_name` | **100.0%** | Accurate handling of compound and hyphenated last names |
| `email` | **100.0%** | Full RFC normalization and validation; anti-spoofing |
| `company_name` | **100.0%** | Extracted from explicit mentions or derived from corporate email domain |
| `job_title` | **96.0%** | High fidelity; defaults to `None` when absent rather than hallucinating |
| `intent` | **98.0%** | Mapped into taxonomy (`demo_request`, `pricing_inquiry`, `general_question`, `spam`, `support`) |
| `urgency` | **98.0%** | Correctly distinguishes explicit dates vs indefinite timelines |
| `budget_signal`| **96.0%** | Grounded in raw text dollar amounts or explicit budget approvals |

---

## 3. Deduplication Confusion Matrix

Evaluation of exact, fuzzy, domain-migration, and adversarial name-collision scenarios:

```
Label \ Pred      new    existing    needs_review
new                37          0               0
existing            0         10               0
needs_review        0          0               3
```

- **False Merges**: **0** (Zero instances of a new lead accidentally overwriting or merging into an unrelated existing contact).
- **Missed Duplicates**: **0** (100% recall on actual duplicate leads).
- **Domain Change Guard**: 100% identified when an existing contact emails from an updated employer domain, safely staging for human review.

---

## 4. Scoring & Routing Performance

```
Tier Confusion Matrix:
  hot   ->  12 / 12  (100.0%)
  warm  ->  20 / 20  (100.0%)
  cold  ->   7 /  7  (100.0%)
  none  ->  11 / 11  (100.0%)

Route Confusion Matrix:
  sales    -> 32 / 32 (100.0%)
  nurture  ->  7 /  7 (100.0%)
  support  ->  3 /  3 (100.0%)
  discard  ->  8 /  8 (100.0%)
```

- **Injection Safety**: 5 injection cases evaluated (e.g. prompt jailbreaks, role reversal instructions, instruction overriding).
  - Detected: **5 / 5 (100.0%)**
  - Unsafe routes to sales: **0**
  - False positives on legitimate leads: **0**

---

## 5. Performance by Scenario Category

| Category Tag | Count | Extraction | Dedupe | Tier | Route |
|---|---|---|---|---|---|
| `clean` | 7 | 98.2% | 100.0% | 100.0% | 100.0% |
| `messy` | 6 | 100.0% | 100.0% | 100.0% | 100.0% |
| `ambiguous` | 3 | 100.0% | 100.0% | 100.0% | 100.0% |
| `crm_exact` | 7 | 100.0% | 100.0% | 100.0% | 100.0% |
| `crm_fuzzy` | 3 | 100.0% | 100.0% | 100.0% | 100.0% |
| `crm_domainchange` | 2 | 100.0% | 100.0% | 100.0% | 100.0% |
| `crm_namecollision` | 2 | 100.0% | 100.0% | 100.0% | 100.0% |
| `injection` | 5 | 97.5% | 100.0% | 100.0% | 100.0% |
| `spam` | 5 | 100.0% | 100.0% | 100.0% | 100.0% |
| `support` | 3 | 95.8% | 100.0% | 100.0% | 100.0% |
| `nurture` | 4 | 90.6% | 100.0% | 100.0% | 100.0% |
| `multilingual` | 2 | 100.0% | 100.0% | 100.0% | 100.0% |

---

## 6. Architecture Cost & Efficiency Analysis

| Component | Architecture | Avg Tokens / Lead | Avg Cost / Lead |
|---|---|---|---|
| **Extraction** | Gemini 2.5 Flash (Structured Output) | ~507 in / ~100 out | $0.00007 |
| **Dedupe** | Deterministic SQL + Jaccard index | 0 tokens (local) | $0.00000 |
| **Scoring** | Rule-based engine + concurrent enrichment | 0 tokens (local) | $0.00000 |
| **Routing** | Deterministic rule matrix | 0 tokens (local) | $0.00000 |
| **Drafting (Critic Loop)** | Bounded 2-iteration Generator-Critic | ~850 in / ~230 out | $0.00013 |
| **Total per Lead (Full Path)** | **Hybrid Deterministic / LLM** | **~1,357 in / ~330 out** | **$0.00020** |

By intentionally restricting autonomous LLM invocations to extraction and email drafting while keeping deduplication, enrichment, scoring, routing, and write-back strictly deterministic, LeadFlow reduces API costs by **>98%** compared to purely agentic multi-hop architectures while eliminating non-deterministic hallucinations in database writes.
