# RecallPlug benchmark

**MEASURED (host)**: measured on this computer through the `ollama` backend, not on the ASUS UGen300 / Hailo-10H.

| Item | Value |
|---|---|
| Date | 2026-10-25 13:22:42 |
| Backend | ollama |
| Model | qwen2.5:1.5b |
| OS | Windows-11-10.0.26300-SP0 |
| Python | 3.12.10 |
| Raw results | `results/20261025-132242/results.csv` |

## Scenario: office (MEASURED (host))

5 fake windows; the last two are in: Outlook, Excel. Runs: 30.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.153 | 0.261 | 30 |
| ttft_approx_s | 0.017 | 0.045 | 30 |
| tokens_per_s | 175.92 | 184.92 | 30 |
| host_cpu_pct | 14.65 | 37.2 | 30 |
| host_ram_pct | 28.9 | 29 | 30 |
| pipeline_s | 0.153 | 0.262 | 30 |

| output_used | Runs |
|---|---|
| model | 30 |
| mixed | 0 |
| fallback | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 30 |
| 1 | 0 |

## Scenario: web_heavy (MEASURED (host))

6 fake windows; the last two are in: Chrome, Chrome. Runs: 30.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.114 | 0.17 | 30 |
| ttft_approx_s | 0.019 | 0.025 | 30 |
| tokens_per_s | 183.68 | 189.96 | 30 |
| host_cpu_pct | 13.3 | 27 | 30 |
| host_ram_pct | 28.9 | 29 | 30 |
| pipeline_s | 0.115 | 0.171 | 30 |

| output_used | Runs |
|---|---|
| model | 10 |
| mixed | 20 |
| fallback | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 10 |
| 1 | 20 |

Notes: bullets 1 and 2 are rule-based by design; the model writes bullet 3 only. `output_used` = model when bullet 3 passed the check, mixed when it was replaced, fallback when the backend was unreachable. `wall_s` is the model call only; `pipeline_s` also includes prompt building and checks. Run 1 can include model load time. Window titles and raw model answers are never saved.
