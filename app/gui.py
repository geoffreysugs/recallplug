"""Recall Plug window (tkinter, standard library only).

    python -m app.gui                  # real timers
    python -m app.gui --demo           # "away" after 10 seconds
    python -m app.gui --backend mock   # no Ollama needed

Hotkeys: Ctrl+Alt+P pause/resume, Ctrl+Alt+E export.

All logic lives in app/gui_model.Presenter; this module only builds widgets,
starts worker threads and copies the presenter's state onto the widgets.
Worker threads never touch Tk: they post messages to the presenter's queue,
which the Tk thread drains with after().
"""
from __future__ import annotations

import argparse
import os
import threading
import time
import webbrowser
from typing import Callable, Optional

from . import ask, blocklist, export, hotkey, selfcheck, settings
from .ask import AskResult
from .config import Config
from .engine import RecallEngine
from .gui_model import (ASK_TIMEOUT_S, ASK_WARNING, GREEN, GREY, ORANGE, RED, ButtonState,
                        Presenter)

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
WINDOW_TITLE = "Recall Plug"
FONT = ("Segoe UI", 10)
BUTTONS = ("pause", "export", "edit_blocklist", "reload_blocklist", "settings",
           "check_again", "download", "open_ollama")


class RecallPlugApp:
    """The window. Every collaborator can be injected (tests, screenshots)."""

    def __init__(self, root, cfg: Config, *, skip_check: bool, skip_reason: str = "",
                 blocklist_loader: Optional[Callable] = None,
                 blocklist_path=None,
                 engine_factory: Optional[Callable] = None,
                 check_fn: Callable = selfcheck.check,
                 pull_fn: Callable = selfcheck.ensure_model,
                 hotkey_factory: Optional[Callable] = hotkey.GlobalHotkey,
                 beep: Callable[[bool], None] = hotkey.beep,
                 open_url: Callable[[str], object] = webbrowser.open,
                 open_file: Optional[Callable[[str], object]] = None,
                 backend=None,
                 ask_fn: Callable = ask.ask,
                 ask_timeout_ms: int = ASK_TIMEOUT_S * 1000,
                 confirm_fn: Optional[Callable[[str, str], bool]] = None,
                 export_dir=None,
                 settings_path=None,
                 poll_ms: int = 500, drain_ms: int = 100,
                 clock: Callable[[], float] = time.time) -> None:
        self.root = root
        self.cfg = cfg
        self.presenter = Presenter(cfg, skip_check, skip_reason, clock=clock)
        self._blocklist_loader = blocklist_loader
        self._blocklist_path = blocklist_path or blocklist.DEFAULT_PATH
        self._engine_factory = engine_factory or self._default_engine
        self._check_fn = check_fn
        self._pull_fn = pull_fn
        self._hotkey_factory = hotkey_factory
        self._beep = beep
        self._open_url = open_url
        self._open_file = open_file or getattr(os, "startfile", None)
        self._backend = backend
        self._ask_fn = ask_fn
        self._ask_timeout_ms = ask_timeout_ms
        self._confirm_fn = confirm_fn
        self._export_dir = export_dir
        self._settings_path = settings_path
        self._clock = clock
        self.settings_window = None
        self._poll_ms, self._drain_ms = poll_ms, drain_ms
        self.engine: Optional[RecallEngine] = None
        self.hotkeys: list = []
        self.closed = False
        self._shown_recent: list[str] = []
        self._timers: dict[str, str] = {}
        self._build()

    # ------------------------------------------------------------ widgets
    def _build(self) -> None:
        import tkinter as tk
        from tkinter import font as tkfont, ttk

        self.tk, self.ttk = tk, ttk
        root = self.root
        root.title(WINDOW_TITLE)
        root.geometry("900x600")
        root.minsize(720, 480)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=FONT[0], size=FONT[1])
            except tk.TclError:
                pass
        root.protocol("WM_DELETE_WINDOW", self.close)

        top = ttk.Frame(root, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        status = ttk.Frame(top)
        status.pack(fill="x")
        self.status_labels = {}
        for key in ("engine", "ollama", "model"):
            lbl = tk.Label(status, text="", font=(FONT[0], 10, "bold"), anchor="w")
            lbl.pack(side="left", padx=(0, 18))
            self.status_labels[key] = lbl
        self.message_label = tk.Label(top, text="", anchor="w", justify="left", fg=GREY)
        self.message_label.pack(fill="x", pady=(4, 0))

        bar = ttk.Frame(top)
        bar.pack(fill="x", pady=(6, 0))
        handlers = {
            "pause": self.on_pause, "export": self.on_export,
            "edit_blocklist": self.on_edit_blocklist, "reload_blocklist": self.on_reload_blocklist,
            "settings": self.on_settings, "check_again": self.on_check_again,
            "download": self.on_download, "open_ollama": self.on_open_ollama,
        }
        self.buttons = {}
        for col, name in enumerate(BUTTONS):
            b = ttk.Button(bar, text=name, command=handlers[name])
            b.grid(row=0, column=col, padx=(0, 6))
            self.buttons[name] = b
        self.progress = ttk.Progressbar(bar, length=160, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=len(BUTTONS), padx=(6, 0))
        self.progress.grid_remove()

        middle = ttk.PanedWindow(root, orient="horizontal")
        middle.pack(fill="both", expand=True, padx=10, pady=6)
        self._middle = middle
        left = ttk.LabelFrame(middle, text="Recent windows", padding=6)
        right = ttk.LabelFrame(middle, text="Latest recap", padding=6)
        middle.add(left, weight=3)
        middle.add(right, weight=2)
        self.recent_list = tk.Listbox(left, activestyle="none", font=FONT, borderwidth=0,
                                      highlightthickness=0)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.recent_list.yview)
        self.recent_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.recent_list.pack(side="left", fill="both", expand=True)
        self.recap_label = tk.Label(right, text="", anchor="nw", justify="left",
                                    wraplength=330, font=FONT)
        self.recap_label.pack(fill="both", expand=True)

        self._build_ask(root)

    def _build_ask(self, root) -> None:
        tk, ttk = self.tk, self.ttk
        box = ttk.LabelFrame(root, text="Ask about your recent windows", padding=6)
        box.pack(fill="x", padx=10, pady=(0, 10))
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.question_var = tk.StringVar()
        self.ask_entry = ttk.Entry(row, textvariable=self.question_var)
        self.ask_entry.pack(side="left", fill="x", expand=True)
        self.ask_entry.bind("<Return>", lambda _e: self.on_ask())
        self.ask_button = ttk.Button(row, text="Ask", command=self.on_ask)
        self.ask_button.pack(side="left", padx=(6, 0))
        tk.Label(box, text=ASK_WARNING, fg=ORANGE, anchor="w").pack(fill="x", pady=(6, 2))
        self.answer_text = tk.Text(box, height=4, wrap="word", font=FONT, relief="flat",
                                   state="disabled", background=root.cget("background"))
        self.answer_text.pack(fill="x")
        self._shown_answer = None

    # ------------------------------------------------------------ start / stop
    def _default_engine(self, on_event):
        return RecallEngine(self.cfg, on_event=on_event, backend=self._backend,
                            blocklist_loader=self._blocklist_loader)

    def start(self) -> None:
        if self.presenter.skip_check:
            self._start_engine()
        else:
            self._run_check()
        self.render()
        self._timers["drain"] = self.root.after(self._drain_ms, self._drain_tick)
        self._timers["poll"] = self.root.after(self._poll_ms, self._poll_tick)

    def _start_engine(self) -> None:
        if self.engine is not None:
            return
        self.engine = self._engine_factory(lambda ev: self.presenter.post("event", ev))
        self.engine.start()
        self.presenter.engine_running()
        self.presenter.set_snapshot(self.engine.snapshot())
        self._start_hotkeys()

    def _start_hotkeys(self) -> None:
        if self._hotkey_factory is None:
            return
        failed = []
        for label, action, vk, hotkey_id, button in (
                ("Ctrl+Alt+P", "toggle_pause", hotkey.VK_P, 1, "Pause"),
                ("Ctrl+Alt+E", "export", ord("E"), 2, "Export")):
            hk = self._hotkey_factory(lambda a=action: self.presenter.post("hotkey", a),
                                      vk=vk, hotkey_id=hotkey_id)
            if hk.start():
                self.hotkeys.append(hk)
            else:
                code = getattr(hk, "win32_error", 0)
                failed.append(f"{label}" + (f" (Win32 error {code})" if code else "")
                              + f"; use the {button} button")
        if failed:
            self.presenter.say("Hotkey not registered: " + ". ".join(failed) + ".", RED)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for timer in self._timers.values():
            try:
                self.root.after_cancel(timer)
            except Exception:
                pass
        if self.engine is not None:
            self.engine.stop()
        for hk in self.hotkeys:
            hk.stop()
        self.hotkeys = []
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------ timers
    def _drain_tick(self) -> None:
        if self.closed:
            return
        for action in self.presenter.drain():
            self.perform(action)
        self.render()
        self._timers["drain"] = self.root.after(self._drain_ms, self._drain_tick)

    def _poll_tick(self) -> None:
        if self.closed:
            return
        if self.engine is not None:
            self.presenter.set_snapshot(self.engine.snapshot())
        self.render()
        self._timers["poll"] = self.root.after(self._poll_ms, self._poll_tick)

    def perform(self, action: str) -> None:
        if action == "start_engine":
            self._start_engine()
        elif action == "recheck":
            self._run_check()
        elif action == "toggle_pause" and self.engine is not None:
            paused = self.engine.toggle_pause()
            threading.Thread(target=self._beep, args=(paused,), daemon=True).start()
            self.presenter.set_snapshot(self.engine.snapshot())
        elif action == "export":
            self.on_export()

    # ------------------------------------------------------------ workers
    def _run_check(self) -> None:
        self.presenter.start_check()

        def work():
            try:
                result = self._check_fn(self.cfg)
            except Exception as exc:          # check() never raises, but be safe
                result = selfcheck.SelfCheckResult(selfcheck.NOT_RUNNING, selfcheck.UNKNOWN,
                                                   self.cfg.model,
                                                   f"Self-check failed ({type(exc).__name__}).")
            self.presenter.post("selfcheck", result)

        threading.Thread(target=work, name="RecallPlug-selfcheck", daemon=True).start()

    # ------------------------------------------------------------ buttons
    def on_ask(self) -> None:
        question = self.question_var.get()
        if self.engine is None or not self.presenter.ask_enabled(question):
            return
        snapshot = self.engine.snapshot()
        ask_id = self.presenter.start_ask()

        def work():
            try:
                result = self._ask_fn(question, snapshot, self._backend, self.cfg)
            except Exception as exc:              # ask() never raises, but be safe
                result = AskResult(f"The question could not be answered ({type(exc).__name__}).",
                                   "unknown")
            self.presenter.post("ask_done", ask_id, result)

        threading.Thread(target=work, name="RecallPlug-ask", daemon=True).start()
        self._timers["ask"] = self.root.after(
            self._ask_timeout_ms, lambda: (self.presenter.ask_timeout(ask_id), self.render()))
        self.render()

    def on_pause(self) -> None:
        if self.engine is not None:
            self.engine.toggle_pause()
            self.presenter.set_snapshot(self.engine.snapshot())
            self.render()

    def _confirm(self, title: str, text: str) -> bool:
        if self._confirm_fn is not None:
            return bool(self._confirm_fn(title, text))
        from tkinter import messagebox
        return bool(messagebox.askokcancel(title, text, parent=self.root, icon="warning"))

    def on_export(self) -> None:
        """The only place titles and a recap are written to disk, after confirmation."""
        if self.engine is None:
            return
        snapshot = self.engine.snapshot()
        if not (snapshot.recent or snapshot.latest_recap):
            self.presenter.say("Nothing to export yet.")
            self.render()
            return
        if not self._confirm("Export", export.WARNING):
            self.presenter.say("Export cancelled.")
            self.render()
            return
        try:
            path = export.export_snapshot(snapshot, self._export_dir, self._clock())
        except OSError as exc:
            self.presenter.say(f"Export failed ({type(exc).__name__}).", RED)
        else:
            self.presenter.say(f"Exported to {path}", GREEN)
        self.render()

    def on_edit_blocklist(self) -> None:
        path = self._blocklist_path
        if not path.exists():
            blocklist.load(path)                      # creates it from the built-in list
        if self._open_file is None:
            self.presenter.say(f"Open this file in a text editor: {path}")
        else:
            try:
                self._open_file(str(path))
                self.presenter.say("Edit and save the block list, then press Reload block list.")
            except OSError as exc:
                self.presenter.say(f"Could not open the block list ({type(exc).__name__}): {path}",
                                   RED)
        self.render()

    def on_reload_blocklist(self) -> None:
        if self.engine is not None:
            n = self.engine.reload_blocklist()
            self.presenter.say(f"Block list reloaded: {n} entries.")
            self.render()

    def on_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.settings_window = SettingsDialog(self, self._settings_path)

    def on_check_again(self) -> None:
        self._run_check()
        self.render()

    def on_download(self) -> None:
        if self.presenter.downloading:
            return
        self.presenter.start_download()

        def work():
            result = self._pull_fn(self.cfg, lambda p, s: self.presenter.post("pull_progress", p, s))
            self.presenter.post("pull_done", result)

        threading.Thread(target=work, name="RecallPlug-pull", daemon=True).start()
        self.render()

    def on_open_ollama(self) -> None:
        self._open_url(OLLAMA_DOWNLOAD_URL)

    # ------------------------------------------------------------ render
    def render(self) -> None:
        if self.closed:
            return
        p = self.presenter
        for key, (text, color) in p.status_labels().items():
            self.status_labels[key].configure(text=text, fg=color)
        self.message_label.configure(text=p.message, fg=p.message_color)
        for name, state in p.buttons().items():
            self._apply_button(self.buttons[name], state)
        if p.downloading:
            self.progress.grid()
            self.progress["value"] = p.download_percent or 0
        else:
            self.progress.grid_remove()
        lines = p.recent_lines()
        if lines != self._shown_recent:
            self.recent_list.delete(0, "end")
            for line in lines:
                self.recent_list.insert("end", line)
            self._shown_recent = lines
        self.ask_entry.configure(state="normal" if p.engine_started else "disabled")
        self.ask_button.configure(
            state="normal" if p.ask_enabled(self.question_var.get()) else "disabled")
        if p.ask_answer != self._shown_answer:
            self.answer_text.configure(state="normal")
            self.answer_text.delete("1.0", "end")
            self.answer_text.insert("1.0", p.ask_answer)
            self.answer_text.configure(state="disabled")
            self._shown_answer = p.ask_answer
        self.recap_label.configure(text="\n\n".join("• " + l if l != "No recap yet" else l
                                                    for l in p.recap()))

    @staticmethod
    def _apply_button(button, state: ButtonState) -> None:
        if state.visible:
            button.grid()
        else:
            button.grid_remove()
        button.configure(text=state.text, state="normal" if state.enabled else "disabled")


class SettingsDialog:
    """Away minutes, demo mode and model name, saved to config/settings.json."""
    NOTE = "Changes apply the next time Recall Plug starts. Command-line flags still win."

    def __init__(self, app: RecallPlugApp, path=None) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app, self.path = app, path
        current, warning = settings.load(path)
        win = tk.Toplevel(app.root)
        win.title("Settings")
        win.transient(app.root)
        win.resizable(False, False)
        self.window = win
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)
        self.away = tk.StringVar(value=str(current.away_minutes))
        self.demo = tk.BooleanVar(value=current.demo)
        self.model = tk.StringVar(value=current.model)
        ttk.Label(body, text="Away after (minutes, 1-60):").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(body, from_=settings.MIN_AWAY, to=settings.MAX_AWAY, width=6,
                    textvariable=self.away).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Checkbutton(body, text="Demo mode (away after 10 seconds)",
                        variable=self.demo).grid(row=1, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Label(body, text="Model name:").grid(row=2, column=0, sticky="w")
        ttk.Entry(body, width=24, textvariable=self.model).grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(body, text=self.NOTE, wraplength=340, foreground=GREY).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.error = tk.Label(body, text=warning or "", fg=RED, wraplength=340, justify="left")
        self.error.grid(row=4, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Save", command=self.save).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Cancel", command=self.close).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", self.close)

    def winfo_exists(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except Exception:
            return False

    def lift(self) -> None:
        self.window.lift()

    def save(self) -> bool:
        data = {"away_minutes": self.away.get().strip(), "demo": bool(self.demo.get()),
                "model": self.model.get()}
        checked, problems = settings.validate(data)
        if problems:
            self.error.configure(text=" ".join(problems))
            return False
        try:
            path = settings.save(checked, self.path)
        except (OSError, ValueError) as exc:
            self.error.configure(text=f"Could not save ({type(exc).__name__}).")
            return False
        self.app.presenter.say(f"Settings saved to {path.name}; they apply at the next start.",
                               GREEN)
        self.close()
        return True

    def close(self) -> None:
        try:
            self.window.destroy()
        except Exception:
            pass
        self.app.settings_window = None


# ------------------------------------------------------------ command line
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m app.gui", description="Recall Plug window")
    ap.add_argument("--demo", action="store_true", help="shorten 'away' to 10 seconds")
    ap.add_argument("--backend", choices=["ollama", "hailo", "mock"],
                    help="inference backend (default from config)")
    ap.add_argument("--no-model", action="store_true", help="rule-only recap, never call a model")
    ap.add_argument("--model", help="Ollama model name (default from config)")
    ap.add_argument("--cpu-only", action="store_true",
                    help="send Ollama num_gpu = 0 so the model runs on the CPU only")
    ap.add_argument("--blocklist", metavar="PATH",
                    help=f"block list file (default: ${blocklist.ENV_VAR} or config/blocklist.txt)")
    return ap


def build_config(args, saved: Optional[settings.Settings] = None) -> Config:
    """Defaults < config/settings.json (`saved`) < command-line flags."""
    cfg = Config()
    if saved is not None:
        settings.apply(cfg, saved)
    if args.model:
        cfg.model = args.model
    if args.backend:
        cfg.backend = args.backend
    if args.no_model:
        cfg.use_model = False
    if args.cpu_only:
        cfg.cpu_only = True
    if args.demo:
        cfg.away_s = 10
    return cfg


def skip_reason(cfg: Config) -> str:
    if not cfg.use_model:
        return "--no-model"
    if cfg.backend == "mock":
        return "mock backend"
    return ""


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    saved, settings_warning = settings.load()
    cfg = build_config(args, saved)
    path = blocklist.resolve_path(args.blocklist)
    first = blocklist.load(path)
    loader = blocklist.make_loader(path, first=first)

    from .win_api import enable_dpi_awareness
    import tkinter as tk

    enable_dpi_awareness()
    root = tk.Tk()
    app = RecallPlugApp(root, cfg, skip_check=not selfcheck.needs_check(cfg),
                        skip_reason=skip_reason(cfg), blocklist_loader=loader,
                        blocklist_path=path)
    if first.warning or settings_warning:
        app.presenter.say("Warning: " + "; ".join(w for w in (first.warning, settings_warning)
                                                   if w), RED)
    app.start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()


if __name__ == "__main__":
    main()
