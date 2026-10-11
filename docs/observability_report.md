# LeadFlow Observability & Cost Summary (Step 13)

## Key Metrics Summary

| Metric | Measured Value | Target / SLA |
|---|---|---|
| **Average Cost per Lead** | **$0.00020** | < $0.02000 |
| **Total Pipeline Runs** | 1 | - |
| **Total Incurred Cost** | $0.0002 | - |
| **Avg Prompt (Input) Tokens** | 1393.0 tokens | < 1,500 |
| **Avg Output Tokens** | 330.0 tokens | < 400 |
| **Avg End-to-End Latency** | 8000.0 ms | < 5,000 ms |

## Pipeline Run Status Breakdown

| Status | Count | Percentage |
|---|---|---|
| `completed` | 1 | 100.0% |


## Per-Step Latency & Reliability Waterfall

| Step | Invocations | Avg Latency | Max Latency | Errors |
|---|---|---|---|---|
| `extract` | 1 | 3226.15 ms | 3226.15 ms | 0 |
| `dedupe` | 1 | 16.19 ms | 16.19 ms | 0 |
| `score` | 1 | 7.59 ms | 7.59 ms | 0 |
| `route` | 1 | 3.45 ms | 3.45 ms | 0 |
| `draft` | 1 | 4338.55 ms | 4338.55 ms | 0 |
| `approval_gate` | 1 | 14.71 ms | 14.71 ms | 0 |
| `write_back` | 1 | 7.41 ms | 7.41 ms | 0 |


## Cost Accounting Breakdown
- **Model**: Google Gemini 2.5 Flash
- **Input Token Rate**: $0.075 / 1M tokens
- **Output Token Rate**: $0.300 / 1M tokens
- **Average Cost Efficiency**: ~105 leads per $0.01 spent (5,000+ leads per $1.00)
