"""Vision-model check on five local screenshots (S1.png .. S5.png).

    python -m tools.vision_eval --images D:\\AI\\test --out D:\\AI\\test\\results --model qwen2.5vl:3b

Runs every image through a local Ollama vision model twice: GPU mode
(default options), then CPU-only mode (options.num_gpu = 0). Before each
mode the model is unloaded (before GPU mode also RecallPlug's text model,
so the GPU is not shared) and the models still loaded are recorded. Each
mode starts with one text-only warm-up call ("Say OK.", no image, so no
image prompt cache exists) that is logged but kept out of the statistics.
During every call the system-wide CPU percent is sampled every 0.1 s
(average and peak), plus peak RAM percent and the peak total RSS of the
Ollama processes ("ollama*" and "*llama-server*"). A call with garbled
output, an error, or (CPU-only) any VRAM use is INVALID and excluded.

The screenshots contain personal information: the images are never
printed or saved, and results go to <out>/<timestamp>/, which must be
OUTSIDE this repository (the tool refuses a folder inside it).
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

try:                       # needed for the CPU / RAM sampling
    import psutil
except Exception:          # pragma: no cover
    psutil = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OLLAMA = "http://127.0.0.1:11434"
IMAGES = ("S1", "S2", "S3", "S4", "S5")
DECISION_IMAGES = ("S1", "S2", "S3")
SAMPLE_STEP_S = 0.1
CALL_TIMEOUT_S = 900
LABEL = "MEASURED (host)"
MODES = {                  # mode -> (display name, extra Ollama options)
    "gpu": ("RTX 4060 Laptop", {}),
    "cpu": ("CPU only (num_gpu=0)", {"num_gpu": 0}),
}
PROMPT = ("Describe in two short sentences what this screen is about: the type of document, "
          "its topic and its structure. Do not copy any numbers, prices, names or email addresses.")
WARMUP_PROMPT = "Say OK."
WARMUP_IMAGE = "warm-up (text only)"
TEXT_MODEL = "qwen2.5:1.5b"           # RecallPlug's recap model, unloaded before GPU mode
GARBLED_MIN_DISTINCT = 3
GARBLED_MAX_SHARE = 0.6

# Pre-registered expectations: written before any run, do not change.
EXPECTED = {
    "S1": {"type": ["document", "text", "table", "spreadsheet", "quote", "notepad"],
           "topic": ["freight", "carrier", "shipping", "price", "quote", "delivery"]},
    "S2": {"type": ["contract", "agreement", "document", "legal"],
           "topic": ["payment", "terms", "clause", "delivery", "termination", "service"]},
    "S3": {"type": ["meeting", "notes", "minutes", "document"],
           "topic": ["action", "task", "project", "status", "sync", "schedule"]},
    "S4": {"type": ["wikipedia", "encyclopedia", "article", "web page", "webpage"],
           "topic": ["rocket", "tsiolkovsky", "equation", "propulsion", "spaceflight", "physics"]},
    "S5": {"type": ["slide", "presentation", "canva", "design"],
           "topic": ["goal", "air quality", "temperature", "humidity", "pressure", "environment"]},
}
DECISION_RULE = ("all three <= 15 s AND CPU peak < 50%  -> away-time capture does not need the "
                 "accelerator; otherwise -> CPU works but interferes, supports moving vision "
                 "inference off the host")
DECISION_PASS = "away-time capture does not need the accelerator"
DECISION_FAIL = "CPU works but interferes, supports moving vision inference off the host"
MAX_SECONDS = 15.0
MAX_CPU_PEAK = 50.0

CSV_FIELDS = ["mode", "mode_name", "image", "warmup", "description", "type_hits", "topic_hits",
              "likely_correct", "copied_digits", "total_duration_s", "load_duration_s",
              "prompt_eval_duration_s", "eval_count", "eval_duration_s", "wall_s",
              "cpu_avg_pct", "cpu_peak_pct", "ram_peak_pct", "ollama_rss_peak_mb",
              "size_mb", "size_vram_mb", "size_vram_ok", "valid", "invalid_reason", "error", "label"]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


# ------------------------------------------------------------ pure parts
def keyword_hits(text: str, words: list[str]) -> list[str]:
    """Keywords found in text, case-insensitive. A keyword must start at a
    word boundary; plurals still match ('quote' finds 'quotes')."""
    low = text.lower()
    return [w for w in words if re.search(r"(?<!\w)" + re.escape(w.lower()), low)]


def check_expected(text: str, image: str) -> tuple[bool, list[str], list[str]]:
    """(likely_correct, type hits, topic hits): likely correct = at least one
    type keyword AND one topic keyword. Only a hint for the human reviewer."""
    exp = EXPECTED[image]
    types, topics = keyword_hits(text, exp["type"]), keyword_hits(text, exp["topic"])
    return bool(types and topics), types, topics


def copied_digits(text: str) -> bool:
    """True if the description contains any digit or an email-like string."""
    return any(ch.isdigit() for ch in text) or bool(_EMAIL.search(text))


def size_vram_ok(mode: str, size_vram) -> bool:
    """CPU-only: the model must use no VRAM at all (size_vram == 0).
    GPU: some of it must be in VRAM. Unknown (model not in /api/ps) is not ok."""
    if size_vram is None or size_vram == "":
        return False
    return int(size_vram) == 0 if mode == "cpu" else int(size_vram) > 0


def garbled(text: str) -> bool:
    """True if the stripped text has fewer than 3 distinct characters or one
    character makes up more than 60% of it (e.g. '@@@@@@')."""
    s = text.strip()
    if len(set(s)) < GARBLED_MIN_DISTINCT:
        return True
    return max(s.count(ch) for ch in set(s)) / len(s) > GARBLED_MAX_SHARE


def invalid_reason(mode: str, size_vram, error: str = "", description: str | None = None) -> str:
    """Why a measured call does not count ("" = it counts): it failed, the
    output is garbled, or a CPU-only call used VRAM (size_vram must be 0).
    Pass description=None to skip the garbled check."""
    if error:
        return "error"
    if description is not None and garbled(description):
        return "garbled output"
    if mode == "cpu" and not size_vram_ok(mode, size_vram):
        return "size_vram not 0" if size_vram not in (None, "") else "size_vram unknown"
    return ""


def call_valid(mode: str, size_vram, error: str = "", description: str | None = None) -> bool:
    return not invalid_reason(mode, size_vram, error, description)


def is_ollama_process(name: str) -> bool:
    """Ollama's own processes: 'ollama*' and its model runner '*llama-server*'."""
    low = (name or "").lower()
    return low.startswith("ollama") or "llama-server" in low


def loaded_cell(models: list[dict] | None) -> str:
    """/api/ps models -> 'name (size MB, VRAM MB), ...' or 'none'."""
    if models is None:
        return "unknown"
    if not models:
        return "none"
    return ", ".join(f"{m.get('name', '?')} ({round(m.get('size', 0) / 2**20)} MB, "
                     f"VRAM {round(m.get('size_vram', 0) / 2**20)} MB)" for m in models)


def decision(rows: list[dict]) -> tuple[str, str]:
    """Pre-registered decision from the valid CPU-only, non-warm-up calls on
    S1, S2 and S3. Uses wall-clock seconds. Returns (result, explanation)."""
    picked = {r["image"]: r for r in rows
              if r["mode"] == "cpu" and not r["warmup"] and r["image"] in DECISION_IMAGES}
    missing = [i for i in DECISION_IMAGES if i not in picked or not picked[i]["valid"]]
    if missing:
        return "UNDETERMINED", f"no valid CPU-only call for {', '.join(missing)}"
    parts, ok = [], True
    for image in DECISION_IMAGES:
        r = picked[image]
        good = float(r["wall_s"]) <= MAX_SECONDS and float(r["cpu_peak_pct"]) < MAX_CPU_PEAK
        ok = ok and good
        parts.append(f"{image}: {fmt(r['wall_s'])} s, CPU peak {fmt(r['cpu_peak_pct'])}%")
    return (DECISION_PASS if ok else DECISION_FAIL), "; ".join(parts)


def mode_stats(rows: list[dict], mode: str) -> dict:
    measured = [r for r in rows if r["mode"] == mode and not r["warmup"]]
    valid = [r for r in measured if r["valid"]]

    def med(key):
        vals = [float(r[key]) for r in valid if r[key] != ""]
        return round(statistics.median(vals), 3) if vals else None

    reasons: dict[str, int] = {}
    for r in measured:
        if not r["valid"]:
            why = r.get("invalid_reason") or "invalid"
            reasons[why] = reasons.get(why, 0) + 1
    return {
        "mode_name": MODES[mode][0],
        "label": LABEL,
        "measured_calls": len(measured),
        "valid_calls": len(valid),
        "invalid_calls": len(measured) - len(valid),
        "invalid_reasons": reasons,
        "likely_correct": sum(bool(r["likely_correct"]) for r in valid),
        "copied_digits": sum(bool(r["copied_digits"]) for r in valid),
        "median_wall_s": med("wall_s"),
        "median_total_duration_s": med("total_duration_s"),
        "median_cpu_avg_pct": med("cpu_avg_pct"),
        "max_cpu_peak_pct": max((float(r["cpu_peak_pct"]) for r in valid if r["cpu_peak_pct"] != ""),
                                default=None),
    }


def fmt(x) -> str:
    if x is None or x == "":
        return "n/a"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        return f"{x:.2f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))
    return str(x)


def md_cell(text) -> str:
    """One table cell: no line breaks, pipes escaped."""
    return " ".join(fmt(text).split()).replace("|", "\\|")


def valid_cell(r: dict) -> str:
    return "yes" if r["valid"] else f"INVALID ({r.get('invalid_reason') or 'invalid'})"


def vram_cell(r: dict) -> str:
    if r["size_vram_mb"] == "":
        return "unknown"
    ok = r["size_vram_ok"]
    if r["mode"] == "cpu":
        return "yes" if ok else f"INVALID ({fmt(r['size_vram_mb'])} MB in VRAM)"
    return f"yes ({fmt(r['size_vram_mb'])} MB)" if ok else "WARN: 0 MB in VRAM"


def expected_cell(image: str) -> str:
    exp = EXPECTED[image]
    return f"type: {', '.join(exp['type'])}; topic: {', '.join(exp['topic'])}"


TABLE_HEAD = ("| image | description | expected | likely_correct | copied_digits | total_duration_s "
              "| wall_s | cpu_avg_pct | cpu_peak_pct | ram_peak_pct | ollama_rss_peak_mb | size_vram_ok "
              "| valid |")


def format_report(rows: list[dict], info: dict, model: str, started: str,
                  loaded_at_start: dict | None = None) -> str:
    loaded_at_start = loaded_at_start or {}
    lines = [
        "# Vision model check on five screenshots",
        "",
        f"Model: **Qwen2.5-VL 3B** (`{model}`) through Ollama. It is a stand-in for the "
        "datasheet's **Qwen2-VL-2B**, not the same model. All results are from **one machine** "
        f"and every number below is **{LABEL}**, not measured on the ASUS UGen300 / Hailo-10H.",
        "",
        "## System",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Started | {started} |",
    ]
    for key in ("CPU", "Logical CPUs", "RAM", "GPU", "Windows", "Ollama", "Python"):
        lines.append(f"| {key} | {md_cell(info.get(key, 'unknown'))} |")
    lines += [
        "",
        f"Prompt (same for every call): \"{PROMPT}\"",
        "Options: stream=false, temperature 0.1, num_predict 80. Before each mode the model was "
        f"unloaded (keep_alive 0) until /api/ps no longer listed it; before GPU mode {TEXT_MODEL} "
        "was unloaded too, so the GPU was not shared. Each mode starts with a text-only warm-up "
        f"(\"{WARMUP_PROMPT}\", no image), so no measured image is in the prompt cache.",
        "",
    ]
    for mode, (name, _opts) in MODES.items():
        mode_rows = [r for r in rows if r["mode"] == mode]
        lines += [f"## {name} ({LABEL})", "",
                  f"Models loaded at the start of this mode (GET /api/ps, before the warm-up): "
                  f"{md_cell(loaded_cell(loaded_at_start.get(mode)))}", ""]
        for r in (r for r in mode_rows if r["warmup"]):
            lines.append(f"Warm-up, {r['image']} (excluded from the statistics): "
                         f"wall {fmt(r['wall_s'])} s, load {fmt(r['load_duration_s'])} s, "
                         f"CPU peak {fmt(r['cpu_peak_pct'])}%, size_vram_ok {vram_cell(r)}"
                         + (f", error: {md_cell(r['error'])}" if r["error"] else "") + ".")
        lines += ["", TABLE_HEAD, "|" + "---|" * 13]
        for r in (r for r in mode_rows if not r["warmup"]):
            desc = r["description"] if not r["error"] else f"ERROR: {r['error']}"
            lines.append("| " + " | ".join(md_cell(v) for v in (
                r["image"], desc, expected_cell(r["image"]), r["likely_correct"], r["copied_digits"],
                r["total_duration_s"], r["wall_s"], r["cpu_avg_pct"], r["cpu_peak_pct"],
                r["ram_peak_pct"], r["ollama_rss_peak_mb"], vram_cell(r), valid_cell(r))) + " |")
        s = mode_stats(rows, mode)
        reasons = ", ".join(f"{n} {why}" for why, n in s["invalid_reasons"].items())
        lines += ["", f"Excluded as INVALID: {s['invalid_calls']} of {s['measured_calls']}"
                  + (f" ({reasons})" if reasons else "") + ". INVALID calls are left out of the "
                  "statistics and of the decision.",
                  f"Valid calls: {s['valid_calls']} of {s['measured_calls']}; likely_correct "
                  f"{s['likely_correct']}, copied_digits {s['copied_digits']}; median wall "
                  f"{fmt(s['median_wall_s'])} s, median CPU average {fmt(s['median_cpu_avg_pct'])}%, "
                  f"highest CPU peak {fmt(s['max_cpu_peak_pct'])}% ({LABEL}).", ""]
    result, why = decision(rows)
    lines += [
        "## Pre-registered decision (CPU-only calls on S1, S2, S3)",
        "",
        f"Rule: \"{DECISION_RULE}\"",
        "",
        f"Seconds are wall-clock seconds per call. Values: {why} ({LABEL}).",
        "",
        f"**Result: {result}**",
        "",
        "## How to read this",
        "",
        "- likely_correct = at least one expected type keyword AND one topic keyword "
        "(case-insensitive). copied_digits = the description contains a digit or an "
        "email-like string. Both are hints only; the final judgement is made by a human.",
        "- INVALID = the call failed, the output is garbled (fewer than 3 distinct characters, "
        "or one character is more than 60% of it), or a CPU-only call had size_vram above 0.",
        "- The expectations were written before any run and were not changed.",
        "- CPU peak is the highest 0.1 s sample of system-wide CPU during the call; the "
        "average covers the whole call. The ollama RSS is the sum over processes whose name "
        "starts with \"ollama\" or contains \"llama-server\" (Ollama's model runner).",
        "- No image was printed or saved; this folder holds only text results.",
        "",
    ]
    return "\n".join(lines)


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def find_images(folder: Path) -> dict[str, Path]:
    """{'S1': path, ...} for S1.png..S5.png, case-insensitive."""
    found = {}
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() == ".png" and p.stem.upper() in IMAGES:
            found[p.stem.upper()] = p
    return found


def same_model(a: str, b: str) -> bool:
    def norm(n: str) -> str:
        return n if ":" in n else n + ":latest"
    return norm(a) == norm(b)


# ------------------------------------------------------------ Ollama
def _get(path: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(OLLAMA + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def installed(model: str) -> bool:
    return any(same_model(m.get("name", ""), model) for m in _get("/api/tags").get("models", []))


def loaded(model: str) -> dict | None:
    for m in _get("/api/ps").get("models", []):
        if same_model(m.get("name", ""), model) or same_model(m.get("model", ""), model):
            return m
    return None


def unload(model: str, wait_s: float = 120) -> None:
    _post("/api/generate", {"model": model, "keep_alive": 0}, timeout=60)
    end = time.time() + wait_s
    while loaded(model) is not None:
        if time.time() > end:
            raise RuntimeError(f"{model} still listed in /api/ps after {wait_s:.0f} s")
        time.sleep(0.5)


# ------------------------------------------------------------ sampling
def _busy(before, after) -> float:
    total = sum(after) - sum(before)
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1 - (after.idle - before.idle) / total)))


def _ollama_rss() -> int:
    rss = 0
    for p in psutil.process_iter(["name", "memory_info"]):
        if is_ollama_process(p.info.get("name")) and p.info.get("memory_info"):
            rss += p.info["memory_info"].rss
    return rss


class Sampler:
    """System-wide CPU every 0.1 s (average over the call + peak), peak RAM
    percent and peak total RSS of ollama* processes."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.cpu: list[float] = []
        self.ram_peak = 0.0
        self.rss_peak = 0

    def __enter__(self) -> "Sampler":
        self._first = psutil.cpu_times()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        last = self._first
        while True:
            self.ram_peak = max(self.ram_peak, psutil.virtual_memory().percent)
            self.rss_peak = max(self.rss_peak, _ollama_rss())
            if self._stop.wait(SAMPLE_STEP_S):
                break
            now = psutil.cpu_times()
            self.cpu.append(_busy(last, now))
            last = now
        self._last_end = psutil.cpu_times()
        if not self.cpu:
            self.cpu.append(_busy(last, self._last_end))

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self.avg = round(_busy(self._first, self._last_end), 1)
        self.peak = round(max(self.cpu), 1)


# ------------------------------------------------------------ one call
def one_call(model: str, mode: str, image: str, path: Path | None) -> dict:
    """One measured image call, or the text-only warm-up when path is None."""
    warmup = path is None
    body = {"model": model, "stream": False,
            "options": {"temperature": 0.1, "num_predict": 80, **MODES[mode][1]}}
    if warmup:
        body["prompt"] = WARMUP_PROMPT
    else:
        body["prompt"] = PROMPT
        body["images"] = [base64.b64encode(path.read_bytes()).decode("ascii")]
    row = {k: "" for k in CSV_FIELDS}
    row.update({"mode": mode, "mode_name": MODES[mode][0], "image": image, "warmup": warmup,
                "label": LABEL})
    t0 = time.perf_counter()
    try:
        with Sampler() as s:
            data = _post("/api/generate", body, timeout=CALL_TIMEOUT_S)
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        data = {}
    row["wall_s"] = round(time.perf_counter() - t0, 3)
    if not row["error"]:
        row.update({"cpu_avg_pct": s.avg, "cpu_peak_pct": s.peak, "ram_peak_pct": s.ram_peak,
                    "ollama_rss_peak_mb": round(s.rss_peak / 2**20)})
    ns = 1e9
    text = " ".join((data.get("response") or "").split())
    row["description"] = text
    for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
        if key in data:
            row[key + "_s"] = round(data[key] / ns, 3)
    row["eval_count"] = data.get("eval_count", "")
    ps = loaded(model) if not row["error"] else None
    if ps is not None:
        row["size_mb"] = round(ps.get("size", 0) / 2**20)
        row["size_vram_mb"] = round(ps.get("size_vram", 0) / 2**20)
    vram = ps.get("size_vram") if ps is not None else None
    row["size_vram_ok"] = size_vram_ok(mode, vram)
    row["invalid_reason"] = invalid_reason(mode, vram, row["error"], None if warmup else text)
    row["valid"] = not row["invalid_reason"]
    if not warmup:
        ok, types, topics = check_expected(text, image)
        row.update({"likely_correct": ok, "type_hits": ";".join(types),
                    "topic_hits": ";".join(topics), "copied_digits": copied_digits(text)})
    return row


# ------------------------------------------------------------ system info
def system_info() -> dict:
    info = {"Python": platform.python_version(),
            "Windows": f"{platform.platform()} (build {platform.version()})"}
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        info["CPU"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except Exception:
        info["CPU"] = platform.processor() or "unknown"
    info["Logical CPUs"] = psutil.cpu_count()
    info["RAM"] = f"{psutil.virtual_memory().total / 2**30:.1f} GB"
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_VideoController).Name"],
                             capture_output=True, text=True, timeout=20).stdout
        info["GPU"] = ", ".join(line.strip() for line in out.splitlines() if line.strip()) or "unknown"
    except Exception:
        info["GPU"] = "unknown"
    try:
        info["Ollama"] = _get("/api/version").get("version", "unknown")
    except Exception:
        info["Ollama"] = "unknown"
    return info


def new_run_dir(base: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path, n = base / stamp, 2
    while path.exists():
        path, n = base / f"{stamp}_{n}", n + 1
    path.mkdir(parents=True)
    return path


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(images_dir: Path, out_base: Path, model: str) -> Path:
    images = find_images(images_dir)
    missing = [i for i in IMAGES if i not in images]
    if missing:
        raise SystemExit(f"missing images in {images_dir}: {', '.join(m + '.png' for m in missing)}")
    if not installed(model):
        say(f"{model} is not installed - running: ollama pull {model}")
        subprocess.run(["ollama", "pull", model], check=True)
        if not installed(model):
            raise SystemExit(f"{model} is still not listed in /api/tags after the pull")
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    info = system_info()
    rows: list[dict] = []
    loaded_at_start: dict[str, list] = {}
    for mode, (name, _opts) in MODES.items():
        say(f"mode {name}: unloading {model}")
        unload(model)
        if mode == "gpu" and not same_model(TEXT_MODEL, model) and loaded(TEXT_MODEL) is not None:
            say(f"mode {name}: unloading {TEXT_MODEL} so the GPU is not shared")
            unload(TEXT_MODEL)
        loaded_at_start[mode] = _get("/api/ps").get("models", [])
        say(f"mode {name}: loaded at start: {loaded_cell(loaded_at_start[mode])}")
        for image, path in [(WARMUP_IMAGE, None)] + [(i, images[i]) for i in IMAGES]:
            say(f"  {image} ...")
            row = one_call(model, mode, image, path)
            rows.append(row)
            state = "valid" if row["valid"] else f"INVALID ({row['invalid_reason']})"
            say(f"  {image}: {state}, wall {row['wall_s']} s, CPU peak {fmt(row['cpu_peak_pct'])}%, "
                f"VRAM {fmt(row['size_vram_mb'])} MB")
    run_dir = new_run_dir(out_base)
    with open(run_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (run_dir / "report.md").write_text(format_report(rows, info, model, started, loaded_at_start),
                                       encoding="utf-8")
    result, why = decision(rows)
    summary = {"model": model, "stand_in_for": "Qwen2-VL-2B", "label": LABEL, "started": started,
               "system": info, "modes": {m: mode_stats(rows, m) for m in MODES},
               "loaded_at_start": {m: loaded_cell(v) for m, v in loaded_at_start.items()},
               "decision_rule": DECISION_RULE, "decision": result, "decision_values": why}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    say(f"decision: {result}")
    return run_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Vision model check on S1.png..S5.png")
    ap.add_argument("--images", required=True, help="folder with S1.png .. S5.png")
    ap.add_argument("--out", required=True, help="results go to <out>/<timestamp>/ (outside the repo)")
    ap.add_argument("--model", default="qwen2.5vl:3b")
    args = ap.parse_args(argv)
    if psutil is None:
        print("psutil is needed for CPU sampling. Install it with:  python -m pip install psutil")
        return 2
    out = Path(args.out)
    if is_inside(out, PROJECT_ROOT):
        print(f"refusing to write inside the repository ({PROJECT_ROOT}); choose a folder outside it")
        return 2
    run_dir = run(Path(args.images), out, args.model)
    say(f"wrote {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
