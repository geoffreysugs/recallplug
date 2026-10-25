"""RecallPlug - Stage I prototype.

Watches which window you work in (titles only, in memory). When you have
been idle for a while, a local model prepares a 3-bullet recap in the
background. The moment you come back, the recap pops up bottom-right.

    python -m app.main --preview 15   # try it now, no waiting
    python -m app.main --demo         # real flow with a 10-second 'away'
    python -m app.main                # real timers (5 minutes away)
    python -m app.main --debug ...    # also print the raw model answer (terminal only)
    python -m app.main --no-model ... # rule-based bullets only, no model call
"""
from __future__ import annotations

import argparse
import threading
import time

from .config import Config
from .hud import launch_hud
from .summarizer import summarize
from .titles import ActivityLog, clean_title, is_blocked
from .win_api import enable_dpi_awareness, get_foreground_title, get_idle_seconds


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def say(msg: str) -> None:
    print(f"[{stamp()}] {msg}", flush=True)


def format_away(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds} s" if seconds < 90 else f"{round(seconds / 60)} min"


def observe(log: ActivityLog, cfg: Config) -> None:
    """Look at the current window once and log it if allowed."""
    raw = get_foreground_title()
    if is_blocked(raw, cfg):
        return
    title, app = clean_title(raw)
    if log.add(title, app):
        stored = log.recent(1)[0].title            # redacted version
        say(f"logged: {stored}" + (f"  ({app})" if app else ""))


def build_hud_title(away_seconds: float, cfg: Config) -> str:
    text = f"Welcome back - away {format_away(away_seconds)}"
    return text[: cfg.max_title_chars]


def print_debug(result: dict) -> None:
    """Terminal only. Raw answers and titles are never written to a file."""
    d = result.get("debug") or {}
    print(f"   [debug] backend: {d.get('backend')}")
    if d.get("raw") is None:
        print("   [debug] no model answer")
    else:
        print("   [debug] raw model answer:")
        for line in d["raw"].splitlines() or [""]:
            print("   [debug]   | " + line)
    for item in d.get("dropped", []):
        print(f"   [debug] dropped - {item}")
    for item in d.get("retries", []):
        print(f"   [debug] {item}")
    for item in d.get("replaced", []):
        print(f"   [debug] {item}")
    if not d.get("dropped") and not d.get("replaced") and not d.get("retries"):
        print("   [debug] nothing dropped, condensed or replaced")


def precompute(entries, cfg: Config, box: dict, debug: bool = False) -> None:
    started = time.perf_counter()
    box["result"] = summarize(entries, cfg)
    r = box["result"]
    extra = f", ~{r['metrics']['tokens_per_s']} tokens/s" if r["metrics"] else ""
    say(f"recap ready in {time.perf_counter() - started:.1f}s (source: {r['source']}{extra})")
    if debug:
        print_debug(r)


def run_preview(cfg: Config, seconds: int, debug: bool = False) -> None:
    log = ActivityLog(cfg.max_log_entries)
    say(f"PREVIEW: switch between a few windows for {seconds} seconds...")
    end = time.time() + seconds
    while time.time() < end:
        observe(log, cfg)
        time.sleep(cfg.poll_s)
    result = summarize(log.recent(cfg.prompt_entries), cfg)
    say(f"recap source: {result['source']}")
    if debug:
        print_debug(result)
    for b in result["bullets"]:
        print("   -", b)
    launch_hud(build_hud_title(seconds, cfg), result["bullets"], cfg.hud_seconds)
    time.sleep(1)


def run_live(cfg: Config, debug: bool = False) -> None:
    log = ActivityLog(cfg.max_log_entries)
    state = "ACTIVE"
    away_since = 0.0
    box: dict = {"result": None}
    worker: threading.Thread | None = None
    say(f"RecallPlug running. Backend: {cfg.backend}, model: {cfg.model}. Away after {cfg.away_s}s idle. Ctrl+C to stop.")

    while True:
        idle = get_idle_seconds()

        if state == "ACTIVE":
            observe(log, cfg)
            if idle >= cfg.away_s:
                state = "AWAY"
                away_since = time.time() - idle
                box["result"] = None
                worker = threading.Thread(
                    target=precompute,
                    args=(log.recent(cfg.prompt_entries), cfg, box, debug),
                    daemon=True,
                )
                worker.start()
                say("state: ACTIVE -> AWAY (preparing recap in the background)")

        elif state == "AWAY" and idle < cfg.welcome_back_idle_s:
            if worker:
                worker.join(timeout=15)
            result = box["result"] or {"bullets": ["Recap was not ready yet."], "source": "none"}
            away_for = time.time() - away_since
            say(f"state: AWAY -> ACTIVE (away {format_away(away_for)}); showing HUD")
            launch_hud(build_hud_title(away_for, cfg), result["bullets"], cfg.hud_seconds)
            state = "ACTIVE"

        time.sleep(cfg.poll_s)


def main() -> None:
    ap = argparse.ArgumentParser(description="RecallPlug Stage I prototype")
    ap.add_argument("--demo", action="store_true", help="shorten 'away' to 10 seconds")
    ap.add_argument("--away", type=int, help="custom 'away' seconds")
    ap.add_argument("--preview", type=int, nargs="?", const=15, metavar="SECONDS",
                    help="collect windows for N seconds, then show the HUD now")
    ap.add_argument("--model", help="Ollama model name (default from config)")
    ap.add_argument("--backend", choices=["ollama", "hailo", "mock"],
                    help="inference backend (default from config)")
    ap.add_argument("--no-model", action="store_true",
                    help="rule-only bullets, never call a model")
    ap.add_argument("--debug", action="store_true",
                    help="print the raw model answer and why bullets were replaced (terminal only)")
    args = ap.parse_args()

    cfg = Config()
    if args.model:
        cfg.model = args.model
    if args.backend:
        cfg.backend = args.backend
    if args.no_model:
        cfg.use_model = False
        say("NO-MODEL MODE: rule-based bullets only, no model is called.")
    if args.demo:
        cfg.away_s = 10
        say("DEMO MODE: away timer shortened to 10 seconds (real value is 300).")
    if args.away:
        cfg.away_s = args.away

    enable_dpi_awareness()
    try:
        if args.preview is not None:
            run_preview(cfg, args.preview, args.debug)
        else:
            run_live(cfg, args.debug)
    except KeyboardInterrupt:
        say("stopped.")


if __name__ == "__main__":
    main()
