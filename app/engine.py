"""The RecallPlug core as a controllable engine.

`RecallEngine` owns the watch loop that used to live in app/main.py: it logs
window titles (memory only), notices when you are away, prepares a recap in
the background and shows it when you come back. A command line (app/main.py)
or a GUI drives it through a small, thread-safe API:

    engine = RecallEngine(cfg, on_event=handle)
    engine.start()            # loop runs in a background thread
    engine.toggle_pause()     # nothing is logged and no recap starts while paused
    snap = engine.snapshot()  # immutable copy, safe from any thread
    engine.stop()             # ends the loop within about a second

Events passed to `on_event` (StateChanged, RecapReady, EngineError) are small
frozen objects. They are delivered on the thread that caused them (the engine
thread, the recap worker thread, or the caller of pause()/resume()), so a GUI
should hand them to its own thread (for tkinter: a queue polled with after()).
An error event carries a short message only: never a traceback or a title.

Nothing here writes window titles or model answers to disk.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, ClassVar, Iterable, Optional

from .config import Config
from .titles import ActivityLog, clean_title, is_blocked

NOT_READY = "Recap was not ready yet."
STOP_JOIN_S = 2.0          # stop() waits at most this long for the loop thread
JOIN_STEP_S = 0.1          # how often a wait re-checks the stop flag


class State(str, Enum):
    RUNNING = "RUNNING"     # watching windows (the console calls this ACTIVE)
    PAUSED = "PAUSED"       # started, but nothing is logged and no recap starts
    AWAY = "AWAY"           # user idle; a recap is being prepared / waiting to be shown
    STOPPED = "STOPPED"     # loop thread not running


@dataclass(frozen=True)
class WindowEntry:
    ts: float               # time.time()-style timestamp from the engine clock
    title: str              # cleaned and redacted title
    app: str                # e.g. "Excel", "" if unknown


@dataclass(frozen=True)
class Snapshot:
    state: State
    recent: tuple            # tuple[WindowEntry, ...], oldest first, at most cfg.max_log_entries
    latest_recap: Optional[tuple]   # tuple[str, ...] (up to 3 lines) or None
    idle_seconds: float      # idle time the engine last used (0.0 while paused)
    blocklist_size: int


@dataclass(frozen=True)
class StateChanged:
    kind: ClassVar[str] = "state_changed"
    old: State
    new: State


@dataclass(frozen=True)
class RecapReady:
    kind: ClassVar[str] = "recap_ready"
    lines: tuple             # tuple[str, ...], up to 3 lines
    source: str              # 'model' | 'mixed' | 'fallback' | 'rule_only'


@dataclass(frozen=True)
class EngineError:
    kind: ClassVar[str] = "error"
    message: str             # short English text; never a traceback or a title


def format_away(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds} s" if seconds < 90 else f"{round(seconds / 60)} min"


def build_hud_title(away_seconds: float, cfg: Config) -> str:
    text = f"Welcome back - away {format_away(away_seconds)}"
    return text[: cfg.max_title_chars]


def _normalize_keywords(words: Iterable[str]) -> tuple:
    return tuple(w.strip().lower() for w in words if w and w.strip())


class RecallEngine:
    """See the module docstring. Every collaborator can be injected for tests:

    get_title()        -> str     foreground window title
    get_idle()         -> float   seconds since the last keyboard/mouse input
    backend                       InferenceBackend passed to summarize (None = from cfg)
    clock()            -> float   wall clock, time.time() style
    launch_hud(title, lines, seconds)   shows the recap window
    on_event(event)               receives StateChanged / RecapReady / EngineError
    log(text)                     console line (main passes its timestamped say())
    debug_printer(result)         called with the summarize() result (--debug)
    blocklist_loader() -> iterable of keywords (None = cfg.blacklist_keywords)
    summarize_fn(entries, cfg, backend) -> dict  (default app.summarizer.summarize)
    """

    def __init__(self, cfg: Config, *,
                 get_title: Optional[Callable[[], str]] = None,
                 get_idle: Optional[Callable[[], float]] = None,
                 backend=None,
                 clock: Optional[Callable[[], float]] = None,
                 launch_hud: Optional[Callable] = None,
                 on_event: Optional[Callable] = None,
                 log: Optional[Callable[[str], None]] = None,
                 debug_printer: Optional[Callable[[dict], None]] = None,
                 blocklist_loader: Optional[Callable[[], Iterable[str]]] = None,
                 summarize_fn: Optional[Callable] = None,
                 hud_wait_s: float = 15.0) -> None:
        if get_title is None or get_idle is None:
            from .win_api import get_foreground_title, get_idle_seconds
            get_title = get_title or get_foreground_title
            get_idle = get_idle or get_idle_seconds
        if launch_hud is None:
            from .hud import launch_hud as _launch_hud
            launch_hud = _launch_hud
        if summarize_fn is None:
            from .summarizer import summarize as summarize_fn
        self.cfg = cfg
        self._get_title = get_title
        self._get_idle = get_idle
        self._backend = backend
        self._clock = clock or time.time
        self._launch_hud = launch_hud
        self._on_event = on_event
        self._log = log or (lambda _text: None)
        self._debug_printer = debug_printer
        self._blocklist_loader = blocklist_loader or (lambda: cfg.blacklist_keywords)
        self._summarize = summarize_fn
        self._hud_wait_s = hud_wait_s

        self._lock = threading.Lock()
        self._entries = ActivityLog(cfg.max_log_entries)
        self._running = False
        self._paused = False
        self._away = False
        self._away_since = 0.0
        self._resumed_at: Optional[float] = None
        self._idle = 0.0
        self._episode = 0                  # bumps whenever an away period is cancelled
        self._pending: Optional[dict] = None
        self._latest: Optional[tuple] = None
        self._worker: Optional[threading.Thread] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._keywords: tuple = ()
        try:
            self._keywords = _normalize_keywords(self._blocklist_loader())
        except Exception as exc:
            self._keywords = _normalize_keywords(cfg.blacklist_keywords)
            self._log(f"warning: block list could not be loaded ({type(exc).__name__}); "
                      "using the built-in list")

    # ------------------------------------------------------------ state
    def _state_locked(self) -> State:
        if not self._running:
            return State.STOPPED
        if self._paused:
            return State.PAUSED
        if self._away:
            return State.AWAY
        return State.RUNNING

    @property
    def state(self) -> State:
        with self._lock:
            return self._state_locked()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def snapshot(self) -> Snapshot:
        with self._lock:
            recent = tuple(WindowEntry(e.ts, e.title, e.app)
                           for e in self._entries.recent(self.cfg.max_log_entries))
            return Snapshot(state=self._state_locked(), recent=recent,
                            latest_recap=self._latest, idle_seconds=self._idle,
                            blocklist_size=len(self._keywords))

    # ------------------------------------------------------------ events
    def _emit(self, event) -> None:
        callback = self._on_event
        if callback is None:
            return
        try:
            callback(event)
        except Exception as exc:          # a broken listener must never stop the engine
            self._log(f"warning: event listener failed ({type(exc).__name__}); ignored")

    def _error(self, message: str) -> None:
        self._log(f"error: {message}")
        self._emit(EngineError(message))

    def _changed(self, old: State, new: State) -> None:
        if old != new:
            self._emit(StateChanged(old, new))

    # ------------------------------------------------------------ control
    def start(self) -> bool:
        """Start the loop thread. False if it is already running."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop_evt = threading.Event()
            new = self._state_locked()
            self._thread = threading.Thread(target=self._run, args=(self._stop_evt,),
                                            name="RecallEngine", daemon=True)
            thread = self._thread
        self._changed(State.STOPPED, new)
        thread.start()
        return True

    def stop(self, timeout: float = STOP_JOIN_S) -> bool:
        """Ask the loop to end and wait for it. True if the thread has ended."""
        with self._lock:
            thread, evt = self._thread, self._stop_evt
        if thread is None:
            return True
        evt.set()
        if thread is threading.current_thread():     # called from an event callback
            return False
        thread.join(timeout)
        return not thread.is_alive()

    def pause(self) -> bool:
        """True if this call paused the engine (False if it already was)."""
        return self._set_paused(True)

    def resume(self) -> bool:
        """True if this call resumed the engine (False if it was not paused)."""
        return self._set_paused(False)

    def toggle_pause(self) -> bool:
        """Flip pause; returns the new is_paused value."""
        self._set_paused(None)
        return self.is_paused

    def _set_paused(self, target: Optional[bool]) -> bool:
        with self._lock:
            value = (not self._paused) if target is None else target
            if value == self._paused:
                return False
            old = self._state_locked()
            self._paused = value
            if value:
                # cancel any away period: its recap is dropped and never shown
                self._away = False
                self._episode += 1
                self._pending = None
                self._worker = None
                self._idle = 0.0
            else:
                # time spent paused never counts as idle/away time
                self._resumed_at = self._clock()
            new = self._state_locked()
        if value:
            self._log("paused - windows are not logged and no recap is prepared")
        else:
            self._log("resumed - logging windows again")
        self._changed(old, new)
        return True

    def reload_blocklist(self) -> int:
        """Re-read the block list through the loader; returns the entry count.
        On failure the previous list stays and an error event is sent."""
        try:
            words = _normalize_keywords(self._blocklist_loader())
        except Exception as exc:
            self._error(f"could not reload the block list ({type(exc).__name__})")
            with self._lock:
                return len(self._keywords)
        with self._lock:
            self._keywords = words
        return len(words)

    # ------------------------------------------------------------ the loop
    def _run(self, stop_evt: threading.Event) -> None:
        try:
            while not stop_evt.is_set():
                try:
                    self.step()
                except Exception as exc:
                    self._error(f"watch step failed ({type(exc).__name__})")
                stop_evt.wait(self.cfg.poll_s)
        finally:
            with self._lock:
                old = self._state_locked()
                self._running = False
                if self._away:                # an unfinished away period is dropped
                    self._away = False
                    self._episode += 1
                    self._pending = None
                    self._worker = None
            self._changed(old, State.STOPPED)

    def _effective_idle(self) -> float:
        idle = float(self._get_idle())
        with self._lock:
            resumed_at = self._resumed_at
        if resumed_at is not None:
            idle = min(idle, max(0.0, self._clock() - resumed_at))
        return idle

    def step(self) -> None:
        """One tick of the loop (the thread calls this every cfg.poll_s).
        Public so tests and tools can drive the engine without a thread."""
        with self._lock:
            paused = self._paused
            if paused:
                self._idle = 0.0
        if paused:
            return
        idle = self._effective_idle()
        with self._lock:
            self._idle = idle
            away = self._away
        if not away:
            self.observe()
            if idle >= self.cfg.away_s:
                self._go_away(idle)
        elif idle < self.cfg.welcome_back_idle_s:
            self._come_back()

    def observe(self) -> bool:
        """Look at the current window once and log it if allowed.
        Returns True if a new entry was added."""
        with self._lock:
            if self._paused:
                return False
        raw = self._get_title() or ""
        with self._lock:
            keywords = self._keywords
        if is_blocked(raw, self.cfg, keywords):
            return False
        title, app = clean_title(raw)
        with self._lock:
            if self._paused:
                return False
            added = self._entries.add(title, app, self._clock())
            stored = self._entries.recent(1)[0].title if added else ""
        if added:
            self._log(f"logged: {stored}" + (f"  ({app})" if app else ""))
        return added

    def _go_away(self, idle: float) -> None:
        with self._lock:
            if self._paused or self._away:
                return
            old = self._state_locked()
            self._away = True
            self._episode += 1
            episode = self._episode
            self._away_since = self._clock() - idle
            self._pending = None
            entries = self._entries.recent(self.cfg.prompt_entries)
            worker = threading.Thread(target=self._precompute, args=(entries, episode),
                                      name="RecallEngine-recap", daemon=True)
            self._worker = worker
            new = self._state_locked()
        worker.start()
        self._log("state: ACTIVE -> AWAY (preparing recap in the background)")
        self._changed(old, new)

    def _precompute(self, entries, episode: int) -> None:
        started = time.perf_counter()
        try:
            result = self._summarize(entries, self.cfg, self._backend)
        except Exception as exc:
            self._error(f"recap failed ({type(exc).__name__})")
            return
        lines = tuple(result.get("bullets") or ())
        with self._lock:
            if episode != self._episode:      # paused or stopped meanwhile: drop it
                return
            self._pending = result
            self._latest = lines
        metrics = result.get("metrics")
        extra = f", ~{metrics['tokens_per_s']} tokens/s" if metrics else ""
        self._log(f"recap ready in {time.perf_counter() - started:.1f}s "
                  f"(source: {result.get('source')}{extra})")
        if self._debug_printer:
            self._debug_printer(result)
        self._emit(RecapReady(lines, str(result.get("source"))))

    def _come_back(self) -> None:
        with self._lock:
            worker, episode = self._worker, self._episode
        if worker is not None:
            deadline = time.monotonic() + self._hud_wait_s
            while (worker.is_alive() and not self._stop_evt.is_set()
                   and time.monotonic() < deadline):
                worker.join(JOIN_STEP_S)
        if self._stop_evt.is_set():
            return
        with self._lock:
            if not self._away or self._paused or episode != self._episode:
                return
            result = self._pending or {"bullets": [NOT_READY], "source": "none"}
            away_for = self._clock() - self._away_since
            old = self._state_locked()
            self._away = False
            self._worker = None
            new = self._state_locked()
        self._log(f"state: AWAY -> ACTIVE (away {format_away(away_for)}); showing HUD")
        try:
            self._launch_hud(build_hud_title(away_for, self.cfg), result["bullets"],
                             self.cfg.hud_seconds)
        except Exception as exc:
            self._error(f"could not open the recap window ({type(exc).__name__})")
        self._changed(old, new)

    # ------------------------------------------------------------ on demand
    def recap_now(self) -> Optional[dict]:
        """Prepare a recap from the recent windows right now (blocking).
        Returns the summarize() result, or None while paused or on failure."""
        with self._lock:
            if self._paused:
                return None
            entries = self._entries.recent(self.cfg.prompt_entries)
        try:
            result = self._summarize(entries, self.cfg, self._backend)
        except Exception as exc:
            self._error(f"recap failed ({type(exc).__name__})")
            return None
        lines = tuple(result.get("bullets") or ())
        with self._lock:
            self._latest = lines
        self._emit(RecapReady(lines, str(result.get("source"))))
        return result
