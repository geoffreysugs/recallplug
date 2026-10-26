"""Benchmark the summary pipeline on built-in fake scenarios.

    python -m bench.run_bench --runs 10
    python -m bench.run_bench --runs 10 --backend mock --scenario web_heavy
    python -m bench.run_bench --runs 10 --show-rejections   # why bullets were replaced (terminal only)
    python -m bench.run_bench --runs 10 --no-model          # rule-only baseline
    python -m bench.run_bench --runs 10 --cpu-only          # Ollama with num_gpu = 0

Every invocation is saved to bench/results/<timestamp>/ (results.csv, one row
per run, and summary.md); bench/summary.md is a copy of the latest summary.
The scenario titles are fake and are never written to any file.
"""
from __future__ import annotations

import argparse
import csv
import math
import platform
import statistics
import time
from pathlib import Path

from app.config import Config
from app.inference import get_backend
from app.summarizer import summarize
from app.titles import Entry

LABEL = "MEASURED (host)"
CPU_ONLY_LABEL = "MEASURED (host, CPU only)"
BENCH_DIR = Path(__file__).resolve().parent

# Fake English scenarios, oldest first: (title, app)
SCENARIOS = {
    # office documents at the end
    "office": [
        ("Supplier Price List 2026 - Shared Drive", "Chrome"),
        ("Q3 Supplier Forecast.xlsx", "Excel"),
        ("Contract_Draft_v2.docx", "Word"),
        ("Re: Delivery schedule for October", "Outlook"),
        ("Q3 Supplier Forecast.xlsx", "Excel"),
    ],
    # .txt files first, the last two windows are web pages
    "web_heavy": [
        ("Meeting Notes.txt", "Notepad"),
        ("Contract Draft v2.txt", "Notepad"),
        ("Forecast.txt", "Notepad"),
        ("Q3 Supplier.txt", "Notepad"),
        ("Freight Quote Calculator - Carrier Portal", "Chrome"),
        ("Supplier Reviews and Ratings | Trade Forum", "Chrome"),
    ],
}

FIELDS = ["scenario", "run", "backend", "model", "wall_s", "ttft_approx_s", "tokens",
          "tokens_per_s", "host_cpu_pct", "host_cpu_peak_pct", "host_ram_pct", "pipeline_s",
          "output_used", "bullets_replaced", "condense_retries", "next_names_last_two",
          "cpu_only", "label"]
STAT_FIELDS = ["wall_s", "ttft_approx_s", "tokens_per_s", "host_cpu_pct", "host_cpu_peak_pct",
               "host_ram_pct", "pipeline_s"]
OUTPUTS = ("model", "mixed", "fallback", "rule_only")


def label_for(cfg: Config) -> str:
    return CPU_ONLY_LABEL if cfg.cpu_only else LABEL


def scenario_entries(name: str) -> list[Entry]:
    windows = SCENARIOS[name]
    start = time.time() - 60 * len(windows)
    return [Entry(start + 60 * i, title, app) for i, (title, app) in enumerate(windows)]


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0-100)."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def print_rejections(result: dict) -> None:
    """Terminal only: why bullets were replaced or condensed."""
    d = result.get("debug") or {}
    for item in d.get("retries", []) + d.get("replaced", []):
        print(f"    - {item}")


def run_bench(runs: int, cfg: Config, scenarios: list[str] | None = None,
              show_rejections: bool = False) -> list[dict]:
    cfg.metrics_csv = ""                 # the bench writes its own files
    backend = get_backend(cfg) if cfg.use_model else None
    rows = []
    for name in scenarios or list(SCENARIOS):
        entries = scenario_entries(name)
        for i in range(1, runs + 1):
            t0 = time.perf_counter()
            result = summarize(entries, cfg, backend)
            pipeline_s = round(time.perf_counter() - t0, 3)
            m = result["metrics"] or {}
            source = result["source"]
            rows.append({
                "scenario": name,
                "run": i,
                "backend": backend.name if backend else "none",
                "model": m.get("model", cfg.model if backend else ""),
                **{k: m.get(k, "") for k in ("wall_s", "ttft_approx_s", "tokens", "tokens_per_s",
                                             "host_cpu_pct", "host_cpu_peak_pct", "host_ram_pct")},
                "pipeline_s": pipeline_s,
                "output_used": source,
                # unreachable model: both model bullets became rule-based; rule_only: n/a
                "bullets_replaced": m.get("bullets_replaced", 2 if source == "fallback" else ""),
                "condense_retries": m.get("condense_retries", 0),
                "next_names_last_two": m.get("next_names_last_two", ""),
                "cpu_only": str(cfg.cpu_only).lower(),
                "label": label_for(cfg),
            })
            print(f"{name} run {i}/{runs}: {source}, {pipeline_s}s")
            if show_rejections:
                print_rejections(result)
    return rows


def write_results(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _fmt(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))


def _scenario_section(name: str, rows: list[dict], label: str) -> list[str]:
    windows = SCENARIOS.get(name, [])
    apps = ", ".join(app for _title, app in windows[-2:])
    lines = [
        f"## Scenario: {name} ({label})",
        "",
        f"{len(windows)} fake windows; the last two are in: {apps}. Runs: {len(rows)}.",
        "",
        "| Metric | Median | p95 | Samples |",
        "|---|---|---|---|",
    ]
    for field in STAT_FIELDS:
        values = [float(r[field]) for r in rows if r[field] != ""]
        if values:
            lines.append(f"| {field} | {_fmt(statistics.median(values))} | "
                         f"{_fmt(percentile(values, 95))} | {len(values)} |")
        else:
            lines.append(f"| {field} | n/a | n/a | 0 |")
    lines += ["", "| output_used | Runs |", "|---|---|"]
    for out in OUTPUTS:
        lines.append(f"| {out} | {sum(r['output_used'] == out for r in rows)} |")
    for field in ("bullets_replaced", "condense_retries"):
        lines += ["", f"| {field} | Runs |", "|---|---|"]
        for n in (0, 1, 2):
            lines.append(f"| {n} | {sum(str(r[field]) == str(n) for r in rows)} |")
    lines += ["", "| next_names_last_two (accepted bullet 3) | Runs |", "|---|---|"]
    for value, label in (("true", "true"), ("false", "false"), ("", "n/a (replaced)")):
        lines.append(f"| {label} | {sum(r['next_names_last_two'] == value for r in rows)} |")
    return lines + [""]


def build_summary(rows: list[dict], cfg: Config, run_dir: str = "") -> str:
    backend = rows[0]["backend"] if rows else cfg.backend
    model = (rows[0]["model"] if rows else cfg.model) or "none (rule-only)"
    label = rows[0]["label"] if rows else label_for(cfg)
    cpu_only = rows[0]["cpu_only"] if rows else str(cfg.cpu_only).lower()
    how = " with Ollama option num_gpu = 0" if cpu_only == "true" else ""
    lines = [
        "# RecallPlug benchmark",
        "",
        f"**{label}**: measured on this computer through the `{backend}` backend{how}, "
        "not on the ASUS UGen300 / Hailo-10H.",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Date | {time.strftime('%Y-%m-%d %H:%M:%S')} |",
        f"| Backend | {backend} |",
        f"| Model | {model} |",
        f"| OS | {platform.platform()} |",
        f"| Python | {platform.python_version()} |",
        f"| Bullet 3 ground windows | {cfg.next_ground_windows} |",
        f"| cpu_only | {cpu_only} |",
    ]
    if run_dir:
        lines.append(f"| Raw results | `{run_dir}/results.csv` |")
    lines.append("")
    for name in dict.fromkeys(r["scenario"] for r in rows):
        lines += _scenario_section(name, [r for r in rows if r["scenario"] == name], label)
    lines += [
        "Notes: bullet 1 is rule-based by design; the model writes bullets 2 and 3. "
        "`output_used` = model when both passed the checks, mixed when at least one was "
        "replaced, fallback when the backend was unreachable, rule_only with --no-model. "
        "`condense_retries` counts the extra model calls made to shorten a long sentence. "
        "Bullet 3 must name one of the last N windows (N = ground windows); "
        "`next_names_last_two` says whether an accepted bullet 3 would also pass N = 2. "
        "`wall_s` is the first model call only; `pipeline_s` also includes condense "
        "retries, prompt building and checks. Run 1 can include model load time. "
        "`host_cpu_pct` is the system-wide average CPU during the first model call and "
        "`host_cpu_peak_pct` the highest 0.1 s sample during it (both need psutil). "
        "Window titles and raw model answers are never saved.",
        "",
    ]
    return "\n".join(lines)


def new_run_dir(base: Path) -> Path:
    """bench/results/<timestamp>/, never reusing an existing folder."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = base / "results" / stamp
    n = 2
    while path.exists():
        path = base / "results" / f"{stamp}_{n}"
        n += 1
    path.mkdir(parents=True)
    return path


def save_run(rows: list[dict], cfg: Config, base: Path) -> Path:
    run_dir = new_run_dir(base)
    write_results(rows, run_dir / "results.csv")
    summary = build_summary(rows, cfg, run_dir.relative_to(base).as_posix())
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    (base / "summary.md").write_text(summary, encoding="utf-8")        # latest
    return run_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="RecallPlug benchmark")
    ap.add_argument("--runs", type=int, default=10, help="runs per scenario")
    ap.add_argument("--backend", help="ollama | hailo | mock (default from config)")
    ap.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    ap.add_argument("--no-model", action="store_true", help="rule-only bullets, no model call")
    ap.add_argument("--cpu-only", action="store_true",
                    help="send Ollama num_gpu = 0 so the model runs on the CPU only")
    ap.add_argument("--ground-windows", type=int, metavar="N",
                    help="bullet 3 must name one of the last N windows (default from config; 2 = strict)")
    ap.add_argument("--show-rejections", action="store_true",
                    help="print why bullets were replaced or condensed (terminal only)")
    ap.add_argument("--out-dir", default=str(BENCH_DIR),
                    help="base folder for results/<timestamp>/ and the latest summary.md")
    args = ap.parse_args()

    cfg = Config()
    if args.backend:
        cfg.backend = args.backend
    if args.no_model:
        cfg.use_model = False
    if args.cpu_only:
        cfg.cpu_only = True
    if args.ground_windows is not None:
        cfg.next_ground_windows = max(1, args.ground_windows)
    scenarios = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    base = Path(args.out_dir)
    base.mkdir(parents=True, exist_ok=True)
    rows = run_bench(max(1, args.runs), cfg, scenarios, args.show_rejections)
    run_dir = save_run(rows, cfg, base)
    print(f"wrote {run_dir / 'results.csv'}, {run_dir / 'summary.md'} and {base / 'summary.md'}")


if __name__ == "__main__":
    main()
