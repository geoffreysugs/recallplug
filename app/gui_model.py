"""Presenter for the Recall Plug window: all GUI logic, no tkinter.

Worker threads (engine events, self-check, model download, hotkeys, ask)
only ever call `post()`, which puts a message on a queue.Queue. The Tk main
thread calls `drain()` from an after() timer, so Tk is only touched from its
own thread. `drain()` returns the actions the window must carry out
(for example "toggle_pause" after the hotkey), and everything the window shows
is computed here from plain data, so it can be tested without Tk.
"""
from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Optional

from . import selfcheck
from .engine import EngineError, RecapReady, Snapshot, State, StateChanged

GREEN = "#1a7f37"
ORANGE = "#b35900"
BLUE = "#0969da"
RED = "#cf222e"
GREY = "#6e7781"

MAX_RECENT = 25
NO_RECAP = "No recap yet"

ENGINE_LABELS = {
    State.RUNNING: ("Recording", GREEN),
    State.PAUSED: ("Paused", ORANGE),
    State.AWAY: ("Away", BLUE),
    State.STOPPED: ("Stopped", GREY),
}


@dataclass(frozen=True)
class ButtonState:
    visible: bool
    enabled: bool
    text: str


def engine_label(state: Optional[State]) -> tuple[str, str]:
    if state is None:
        return "Not started", GREY
    return ENGINE_LABELS.get(state, (str(state), GREY))


def ollama_label(ollama_state: Optional[str], skipped: bool) -> tuple[str, str]:
    if skipped:
        return "Ollama: not needed", GREY
    if ollama_state is None:
        return "Ollama: checking...", GREY
    if ollama_state == selfcheck.RUNNING:
        return "Ollama: running", GREEN
    return "Ollama: not running", RED


def model_label(model_state: Optional[str], name: str, skipped: bool,
                skip_reason: str = "") -> tuple[str, str]:
    if skipped:
        return f"Model: off ({skip_reason})" if skip_reason else "Model: off", GREY
    if model_state is None:
        return "Model: checking...", GREY
    if model_state == selfcheck.PRESENT:
        return f"Model: {name}", GREEN
    if model_state == selfcheck.MISSING:
        return f"Model: {name} missing", RED
    return f"Model: {name} (unknown)", ORANGE


def relative_time(ts: float, now: float) -> str:
    """'just now', '42 s ago', '3 min ago', '2 h ago'."""
    seconds = max(0, int(now - ts))
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def format_recent(entries, now: float, limit: int = MAX_RECENT) -> list[str]:
    """Snapshot entries (oldest first) -> display lines, newest first."""
    lines = []
    for e in list(entries)[-limit:][::-1]:
        label = f"{e.title} ({e.app})" if e.app else e.title
        lines.append(f"{relative_time(e.ts, now):>10}   {label}")
    return lines


def recap_lines(snapshot: Optional[Snapshot]) -> list[str]:
    if snapshot is None or not snapshot.latest_recap:
        return [NO_RECAP]
    return list(snapshot.latest_recap)


class Presenter:
    """Holds what the window shows. Only the Tk thread may call anything but post()."""

    def __init__(self, cfg, skip_check: bool, skip_reason: str = "",
                 clock=time.time) -> None:
        self.cfg = cfg
        self.skip_check = skip_check
        self.skip_reason = skip_reason
        self.clock = clock
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.ollama_state: Optional[str] = None
        self.model_state: Optional[str] = None
        self.checking = False
        self.downloading = False
        self.download_percent: Optional[float] = None
        self.download_status = ""
        self.engine_started = False
        self.engine_state: Optional[State] = None
        self.snapshot: Optional[Snapshot] = None
        self.message = ""
        self.message_color = GREY
        self.ask_busy = False
        self.ask_answer = ""
        self.ask_source: Optional[str] = None

    # ------------------------------------------------------------ any thread
    def post(self, kind: str, *payload) -> None:
        self.queue.put((kind,) + payload)

    # ------------------------------------------------------------ Tk thread
    def drain(self, max_items: int = 200) -> list[str]:
        """Apply queued messages; returns the actions the window must perform."""
        actions: list[str] = []
        for _ in range(max_items):
            try:
                msg = self.queue.get_nowait()
            except queue.Empty:
                break
            action = self.apply(msg)
            if action:
                actions.append(action)
        return actions

    def say(self, text: str, color: str = GREY) -> None:
        self.message, self.message_color = text, color

    def apply(self, msg: tuple) -> Optional[str]:
        kind = msg[0]
        if kind == "event":
            ev = msg[1]
            if isinstance(ev, StateChanged):
                self.engine_state = ev.new
            elif isinstance(ev, RecapReady):
                self.say("A new recap is ready.", GREEN)
            elif isinstance(ev, EngineError):
                self.say(f"Error: {ev.message}", RED)
            return None
        if kind == "selfcheck":
            result = msg[1]
            self.checking = False
            self.ollama_state, self.model_state = result.ollama_state, result.model_state
            self.say(result.hint, GREEN if result.ok else RED)
            return "start_engine" if self.can_start_engine() and not self.engine_started else None
        if kind == "pull_progress":
            self.download_percent, self.download_status = msg[1], msg[2] or ""
            if msg[1] is not None:
                self.say(f"Downloading {self.cfg.model}: {msg[1]:.0f}%", BLUE)
            elif msg[2]:
                self.say(f"Downloading {self.cfg.model}: {msg[2]}", BLUE)
            return None
        if kind == "pull_done":
            result = msg[1]
            self.downloading = False
            self.download_percent = None
            self.say(result.message, GREEN if result.ok else RED)
            return "recheck" if result.ok else None
        if kind == "hotkey":
            return msg[1]                        # "toggle_pause" / "export"
        if kind == "ask_done":
            result = msg[1]
            self.ask_busy = False
            self.ask_answer, self.ask_source = result.text, result.source
            return None
        if kind == "info":
            self.say(msg[1], msg[2] if len(msg) > 2 else GREY)
            return None
        return None

    # ------------------------------------------------------------ state
    def can_start_engine(self) -> bool:
        if self.skip_check:
            return True
        return (self.ollama_state == selfcheck.RUNNING
                and self.model_state in (selfcheck.PRESENT, selfcheck.UNKNOWN))

    def start_check(self) -> None:
        self.checking = True
        self.ollama_state = self.model_state = None
        self.say("Checking Ollama and the model...")

    def start_download(self) -> None:
        self.downloading = True
        self.download_percent = 0.0
        self.say(f"Asking Ollama to download {self.cfg.model}...", BLUE)

    def start_ask(self) -> None:
        self.ask_busy = True
        self.ask_answer = "Thinking..."
        self.ask_source = None

    def engine_running(self) -> None:
        self.engine_started = True

    def set_snapshot(self, snap: Snapshot) -> None:
        self.snapshot = snap
        self.engine_state = snap.state

    # ------------------------------------------------------------ view data
    def status_labels(self) -> dict[str, tuple[str, str]]:
        return {
            "ollama": ollama_label(self.ollama_state, self.skip_check),
            "model": model_label(self.model_state, self.cfg.model, self.skip_check,
                                 self.skip_reason),
            "engine": engine_label(self.engine_state if self.engine_started else None),
        }

    def recent_lines(self) -> list[str]:
        if self.snapshot is None:
            return []
        return format_recent(self.snapshot.recent, self.clock())

    def recap(self) -> list[str]:
        return recap_lines(self.snapshot)

    def has_data(self) -> bool:
        s = self.snapshot
        return bool(s and (s.recent or s.latest_recap))

    def ask_enabled(self, question: str) -> bool:
        return self.engine_started and not self.ask_busy and bool(question.strip())

    def buttons(self) -> dict[str, ButtonState]:
        running = self.engine_started and self.engine_state in (State.RUNNING, State.PAUSED,
                                                                State.AWAY)
        problem = (not self.skip_check and not self.checking and
                   (self.ollama_state == selfcheck.NOT_RUNNING
                    or self.model_state in (selfcheck.MISSING, selfcheck.UNKNOWN)))
        busy = self.checking or self.downloading
        return {
            "pause": ButtonState(True, running,
                                 "Resume" if self.engine_state == State.PAUSED else "Pause"),
            "export": ButtonState(True, self.has_data(), "Export"),
            "edit_blocklist": ButtonState(True, True, "Edit block list"),
            "reload_blocklist": ButtonState(True, self.engine_started, "Reload block list"),
            "settings": ButtonState(True, True, "Settings"),
            "check_again": ButtonState(problem, problem and not busy, "Check again"),
            "download": ButtonState(
                not self.skip_check and self.ollama_state == selfcheck.RUNNING
                and self.model_state == selfcheck.MISSING,
                not busy, "Download model"),
            "open_ollama": ButtonState(
                not self.skip_check and self.ollama_state == selfcheck.NOT_RUNNING,
                True, "Open Ollama page"),
        }
