# RecallPlug benchmark

**MEASURED (host)**: measured on this computer through the `ollama` backend, not on the ASUS UGen300 / Hailo-10H.

| Item | Value |
|---|---|
| Date | 2026-10-25 13:14:14 |
| Backend | ollama |
| Model | qwen2.5:1.5b |
| OS | Windows-11-10.0.26300-SP0 |
| Python | 3.12.10 |
| Raw results | `results/20261025-131414/results.csv` |

## Scenario: office (MEASURED (host))

5 fake windows; the last two are in: Outlook, Excel. Runs: 3.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.122 | 2.915 | 3 |
| ttft_approx_s | 0.02 | 2.742 | 3 |
| tokens_per_s | 162.49 | 163.73 | 3 |
| host_cpu_pct | 19.6 | 22.6 | 3 |
| host_ram_pct | 29 | 29 | 3 |
| pipeline_s | 0.123 | 2.915 | 3 |

| output_used | Runs |
|---|---|
| model | 3 |
| mixed | 0 |
| fallback | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 3 |
| 1 | 0 |

## Scenario: web_heavy (MEASURED (host))

6 fake windows; the last two are in: Chrome, Chrome. Runs: 3.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.122 | 0.181 | 3 |
| ttft_approx_s | 0.022 | 0.024 | 3 |
| tokens_per_s | 171.38 | 186.55 | 3 |
| host_cpu_pct | 11.6 | 17.4 | 3 |
| host_ram_pct | 29 | 29 | 3 |
| pipeline_s | 0.122 | 0.182 | 3 |

| output_used | Runs |
|---|---|
| model | 1 |
| mixed | 2 |
| fallback | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 1 |
| 1 | 2 |

Notes: bullets 1 and 2 are rule-based by design; the model writes bullet 3 only. `output_used` = model when bullet 3 passed the check, mixed when it was replaced, fallback when the backend was unreachable. `wall_s` is the model call only; `pipeline_s` also includes prompt building and checks. Run 1 can include model load time. Window titles and raw model answers are never saved.
