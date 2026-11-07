"""RecallPlug - Stage I prototype.

Watches which window you work in (titles only, in memory). When you have
been idle for a while, a local model prepares a 3-bullet recap in the
background. The moment you come back, the recap pops up bottom-right.

    python -m app.main --preview 15   # try it now, no waiting
    python -m app.main --demo         # real flow with a 10-second 'away'
    python -m app.main                # real timers (5 minutes away)
    python -m app.main --debug ...    # also print the raw model answer (terminal only)
    python -m app.main --no-model ... # rule-based bullets only, no model call
    python -m app.main --cpu-only ... # ask Ollama to run the model on the CPU only

The watch loop itself lives in app/engine.py (RecallEngine); this module is
the command-line wrapper around it.
"""
from __future__ import annotations

import argparse
import sys
import time

from . import blocklist, hotkey, selfcheck
from .config import Config
from .engine import RecallEngine, build_hud_title, format_away  # noqa: F401  (re-exported)
from .hud import launch_hud
from .win_api import enable_dpi_awareness


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def say(msg: str) -> None:
    print(f"[{stamp()}] {msg}", flush=True)


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


def make_engine(cfg: Config, debug: bool = False, blocklist_loader=None, **kwargs) -> RecallEngine:
    return RecallEngine(cfg, log=say, debug_printer=print_debug if debug else None,
                        blocklist_loader=blocklist_loader, **kwargs)


def run_preview(cfg: Config, seconds: int, debug: bool = False, blocklist_loader=None) -> None:
    engine = make_engine(cfg, debug=False, blocklist_loader=blocklist_loader)
    say(f"PREVIEW: switch between a few windows for {seconds} seconds...")
    end = time.time() + seconds
    while time.time() < end:
        engine.observe()
        time.sleep(cfg.poll_s)
    result = engine.recap_now() or {"bullets": ["Recap could not be prepared."],
                                    "source": "none", "debug": {}}
    say(f"recap source: {result['source']}")
    if debug:
        print_debug(result)
    for b in result["bullets"]:
        print("   -", b)
    launch_hud(build_hud_title(seconds, cfg), result["bullets"], cfg.hud_seconds)
    time.sleep(1)


def start_pause_hotkey(engine: RecallEngine, hotkey_factory=hotkey.GlobalHotkey,
                       beep=hotkey.beep):
    """Ctrl+Alt+P toggles pause (the engine prints the console line, we beep).
    Returns the started hotkey, or None with one warning if it is unavailable."""
    def on_hotkey():
        beep(engine.toggle_pause())

    hk = hotkey_factory(on_hotkey)
    if hk.start():
        say(f"Pause hotkey: {hotkey.PAUSE_LABEL} pauses and resumes logging.")
        return hk
    code = getattr(hk, "win32_error", 0)
    say(f"warning: the pause hotkey {hotkey.PAUSE_LABEL} could not be registered "
        + (f"(Win32 error {code}; " if code else "(")
        + "another program may use it); continuing without it.")
    return None


def run_live(cfg: Config, debug: bool = False, blocklist_loader=None) -> None:
    engine = make_engine(cfg, debug=debug, blocklist_loader=blocklist_loader)
    say(f"RecallPlug running. Backend: {cfg.backend}, model: {cfg.model}. Away after {cfg.away_s}s idle. Ctrl+C to stop.")
    hk = start_pause_hotkey(engine)
    engine.start()
    try:
        while engine.is_running:
            time.sleep(0.5)
    finally:
        engine.stop()
        if hk:
            hk.stop()


def build_parser() -> argparse.ArgumentParser:
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
    ap.add_argument("--cpu-only", action="store_true",
                    help="send Ollama num_gpu = 0 so the model runs on the CPU only")
    ap.add_argument("--debug", action="store_true",
                    help="print the raw model answer and why bullets were replaced (terminal only)")
    ap.add_argument("--yes", action="store_true",
                    help="answer yes when asked to download a missing model")
    ap.add_argument("--blocklist", metavar="PATH",
                    help=f"block list file (default: ${blocklist.ENV_VAR} or config/blocklist.txt)")
    return ap


def load_blocklist(cli_path=None, environ=None):
    """Loads the block list once, prints the result and returns an engine loader."""
    path = blocklist.resolve_path(cli_path, environ)
    result = blocklist.load(path)
    if result.warning:
        say("warning: " + result.warning)
    elif result.source == "created":
        say(f"block list created with the built-in keywords: {path}")
    say(f"block list: {len(result.keywords)} entries loaded"
        + (" (built-in)" if result.source == "defaults" else f" from {path}"))
    return blocklist.make_loader(path, warn=say, first=result)


def build_config(args) -> Config:
    cfg = Config()
    if args.model:
        cfg.model = args.model
    if args.backend:
        cfg.backend = args.backend
    if args.no_model:
        cfg.use_model = False
        say("NO-MODEL MODE: rule-based bullets only, no model is called.")
    if args.cpu_only:
        cfg.cpu_only = True
        say("CPU-ONLY MODE: Ollama is asked for num_gpu = 0 (the first call may reload the model).")
    if args.demo:
        cfg.away_s = 10
        say("DEMO MODE: away timer shortened to 10 seconds (real value is 300).")
    if args.away:
        cfg.away_s = args.away
    return cfg


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    cfg = build_config(args)

    loader = load_blocklist(args.blocklist)

    enable_dpi_awareness()
    try:
        if selfcheck.needs_check(cfg):
            code = selfcheck.run_cli_check(cfg, assume_yes=args.yes)
            if code:
                sys.exit(code)
        if args.preview is not None:
            run_preview(cfg, args.preview, args.debug, blocklist_loader=loader)
        else:
            run_live(cfg, args.debug, blocklist_loader=loader)
    except KeyboardInterrupt:
        say("stopped.")


if __name__ == "__main__":
    main()
