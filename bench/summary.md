# RecallPlug benchmark

**MEASURED (host)**: measured on this computer through the `ollama` backend, not on the ASUS UGen300 / Hailo-10H.

| Item | Value |
|---|---|
| Date | 2026-10-25 15:09:40 |
| Backend | ollama |
| Model | qwen2.5:1.5b |
| OS | Windows-11-10.0.26300-SP0 |
| Python | 3.12.10 |
| Bullet 3 ground windows | 2 |
| Raw results | `results/20261025-150940/results.csv` |

## Scenario: office (MEASURED (host))

5 fake windows; the last two are in: Outlook, Excel. Runs: 30.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.188 | 0.227 | 30 |
| ttft_approx_s | 0.018 | 0.029 | 30 |
| tokens_per_s | 172.475 | 180.46 | 30 |
| host_cpu_pct | 17.7 | 31 | 30 |
| host_ram_pct | 24.9 | 24.9 | 30 |
| pipeline_s | 0.189 | 0.228 | 30 |

| output_used | Runs |
|---|---|
| model | 30 |
| mixed | 0 |
| fallback | 0 |
| rule_only | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 30 |
| 1 | 0 |
| 2 | 0 |

| condense_retries | Runs |
|---|---|
| 0 | 30 |
| 1 | 0 |
| 2 | 0 |

| next_names_last_two (accepted bullet 3) | Runs |
|---|---|
| true | 30 |
| false | 0 |
| n/a (replaced) | 0 |

## Scenario: web_heavy (MEASURED (host))

6 fake windows; the last two are in: Chrome, Chrome. Runs: 30.

| Metric | Median | p95 | Samples |
|---|---|---|---|
| wall_s | 0.164 | 0.198 | 30 |
| ttft_approx_s | 0.018 | 0.023 | 30 |
| tokens_per_s | 178.61 | 182.12 | 30 |
| host_cpu_pct | 14.9 | 28.3 | 30 |
| host_ram_pct | 24.9 | 24.9 | 30 |
| pipeline_s | 0.164 | 0.199 | 30 |

| output_used | Runs |
|---|---|
| model | 1 |
| mixed | 29 |
| fallback | 0 |
| rule_only | 0 |

| bullets_replaced | Runs |
|---|---|
| 0 | 1 |
| 1 | 29 |
| 2 | 0 |

| condense_retries | Runs |
|---|---|
| 0 | 30 |
| 1 | 0 |
| 2 | 0 |

| next_names_last_two (accepted bullet 3) | Runs |
|---|---|
| true | 1 |
| false | 0 |
| n/a (replaced) | 29 |

Notes: bullet 1 is rule-based by design; the model writes bullets 2 and 3. `output_used` = model when both passed the checks, mixed when at least one was replaced, fallback when the backend was unreachable, rule_only with --no-model. `condense_retries` counts the extra model calls made to shorten a long sentence. Bullet 3 must name one of the last N windows (N = ground windows); `next_names_last_two` says whether an accepted bullet 3 would also pass N = 2. `wall_s` is the first model call only; `pipeline_s` also includes condense retries, prompt building and checks. Run 1 can include model load time. Window titles and raw model answers are never saved.
