# RecallPlug benchmark

**MEASURED (host)**: measured on this computer through the `mock` backend, not on the ASUS UGen300 / Hailo-10H.

| Item | Value |
|---|---|
| Date | 2026-10-25 15:02:47 |
| Backend | mock |
| Model | mock |
| OS | Windows-11-10.0.26300-SP0 |
| Python | 3.12.10 |
| Raw results | `results/20261025-150247_2/results.csv` |

## Scenario: office (MEASURED (host))

5 fake windows; the last two are in: Outlook, Excel. Runs: 5.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0 | 0 | 5 |
| ttft_approx_s | 0 | 0 | 5 |
| tokens_per_s | 0 | 0 | 5 |
| host_cpu_pct | 0 | 100 | 5 |
| host_ram_pct | 24.3 | 24.3 | 5 |
| pipeline_s | 0 | 0.001 | 5 |

| output_used | Runs |
|---|---|
| model | 5 |
| mixed | 0 |
| fallback | 0 |
| rule_only | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 5 |
| 1 | 0 |
| 2 | 0 |

| condense_retries | Runs |
|---|---|
| 0 | 5 |
| 1 | 0 |
| 2 | 0 |

## Scenario: web_heavy (MEASURED (host))

6 fake windows; the last two are in: Chrome, Chrome. Runs: 5.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0 | 0 | 5 |
| ttft_approx_s | 0 | 0 | 5 |
| tokens_per_s | 0 | 0 | 5 |
| host_cpu_pct | 0 | 0 | 5 |
| host_ram_pct | 24.3 | 24.3 | 5 |
| pipeline_s | 0 | 0.001 | 5 |

| output_used | Runs |
|---|---|
| model | 5 |
| mixed | 0 |
| fallback | 0 |
| rule_only | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 5 |
| 1 | 0 |
| 2 | 0 |

| condense_retries | Runs |
|---|---|
| 0 | 5 |
| 1 | 0 |
| 2 | 0 |

Notes: bullet 1 is rule-based by design; the model writes bullets 2 and 3. `output_used` = model when both passed the checks, mixed when at least one was replaced, fallback when the backend was unreachable, rule_only with --no-model. `condense_retries` counts the extra model calls made to shorten a long sentence. `wall_s` is the first model call only; `pipeline_s` also includes condense retries, prompt building and checks. Run 1 can include model load time. Window titles and raw model answers are never saved.
