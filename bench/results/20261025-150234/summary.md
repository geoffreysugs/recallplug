# RecallPlug benchmark

**MEASURED (host)**: measured on this computer through the `ollama` backend, not on the ASUS UGen300 / Hailo-10H.

| Item | Value |
|---|---|
| Date | 2026-10-25 15:02:34 |
| Backend | ollama |
| Model | qwen2.5:1.5b |
| OS | Windows-11-10.0.26300-SP0 |
| Python | 3.12.10 |
| Raw results | `results/20261025-150234/results.csv` |

## Scenario: office (MEASURED (host))

5 fake windows; the last two are in: Outlook, Excel. Runs: 30.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.184 | 0.217 | 30 |
| ttft_approx_s | 0.017 | 0.029 | 30 |
| tokens_per_s | 176.98 | 180.71 | 30 |
| host_cpu_pct | 14.65 | 27.1 | 30 |
| host_ram_pct | 24.2 | 24.2 | 30 |
| pipeline_s | 0.185 | 0.217 | 30 |

| output_used | Runs |
|---|---|
| model | 29 |
| mixed | 1 |
| fallback | 0 |
| rule_only | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 29 |
| 1 | 1 |
| 2 | 0 |

| condense_retries | Runs |
|---|---|
| 0 | 30 |
| 1 | 0 |
| 2 | 0 |

## Scenario: web_heavy (MEASURED (host))

6 fake windows; the last two are in: Chrome, Chrome. Runs: 30.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.167 | 0.198 | 30 |
| ttft_approx_s | 0.018 | 0.028 | 30 |
| tokens_per_s | 178.265 | 182.15 | 30 |
| host_cpu_pct | 12.75 | 27.8 | 30 |
| host_ram_pct | 24.2 | 24.2 | 30 |
| pipeline_s | 0.167 | 0.199 | 30 |

| output_used | Runs |
|---|---|
| model | 0 |
| mixed | 30 |
| fallback | 0 |
| rule_only | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 0 |
| 1 | 30 |
| 2 | 0 |

| condense_retries | Runs |
|---|---|
| 0 | 30 |
| 1 | 0 |
| 2 | 0 |

Notes: bullet 1 is rule-based by design; the model writes bullets 2 and 3. `output_used` = model when both passed the checks, mixed when at least one was replaced, fallback when the backend was unreachable, rule_only with --no-model. `condense_retries` counts the extra model calls made to shorten a long sentence. `wall_s` is the first model call only; `pipeline_s` also includes condense retries, prompt building and checks. Run 1 can include model load time. Window titles and raw model answers are never saved.
