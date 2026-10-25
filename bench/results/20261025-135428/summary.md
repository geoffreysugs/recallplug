# RecallPlug benchmark

**MEASURED (host)**: measured on this computer through the `ollama` backend, not on the ASUS UGen300 / Hailo-10H.

| Item | Value |
|---|---|
| Date | 2026-10-25 13:54:28 |
| Backend | ollama |
| Model | qwen2.5:1.5b |
| OS | Windows-11-10.0.26300-SP0 |
| Python | 3.12.10 |
| Raw results | `results/20261025-135428/results.csv` |

## Scenario: office (MEASURED (host))

5 fake windows; the last two are in: Outlook, Excel. Runs: 10.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.313 | 0.429 | 10 |
| ttft_approx_s | 0.026 | 0.099 | 10 |
| tokens_per_s | 91.905 | 95.81 | 10 |
| host_cpu_pct | 41.15 | 70.5 | 10 |
| host_ram_pct | 28.5 | 28.6 | 10 |
| pipeline_s | 0.314 | 0.43 | 10 |

| output_used | Runs |
|---|---|
| model | 10 |
| mixed | 0 |
| fallback | 0 |
| rule_only | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 10 |
| 1 | 0 |
| 2 | 0 |

| condense_retries | Runs |
|---|---|
| 0 | 10 |
| 1 | 0 |
| 2 | 0 |

## Scenario: web_heavy (MEASURED (host))

6 fake windows; the last two are in: Chrome, Chrome. Runs: 10.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.287 | 0.346 | 10 |
| ttft_approx_s | 0.022 | 0.046 | 10 |
| tokens_per_s | 91.54 | 94.36 | 10 |
| host_cpu_pct | 45.55 | 53.9 | 10 |
| host_ram_pct | 28.7 | 28.8 | 10 |
| pipeline_s | 0.288 | 0.348 | 10 |

| output_used | Runs |
|---|---|
| model | 0 |
| mixed | 10 |
| fallback | 0 |
| rule_only | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 0 |
| 1 | 10 |
| 2 | 0 |

| condense_retries | Runs |
|---|---|
| 0 | 10 |
| 1 | 0 |
| 2 | 0 |

Notes: bullet 1 is rule-based by design; the model writes bullets 2 and 3. `output_used` = model when both passed the checks, mixed when at least one was replaced, fallback when the backend was unreachable, rule_only with --no-model. `condense_retries` counts the extra model calls made to shorten a long sentence. `wall_s` is the first model call only; `pipeline_s` also includes condense retries, prompt building and checks. Run 1 can include model load time. Window titles and raw model answers are never saved.
