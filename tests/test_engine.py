"""RecallEngine: state machine, pause/resume, events, snapshot, thread.

No real windows, idle timer, HUD process or model: everything is injected."""
import dataclasses
import threading
import time
import unittest

from app.config import Config
from app.engine import (NOT_READY, EngineError, RecallEngine, RecapReady, Snapshot, State,
                        StateChanged, WindowEntry, build_hud_title, format_away)
from app.inference.mock_backend import MockBackend


class FakeDesk:
    """Fake window watcher, idle timer and clock."""

    def __init__(self):
        self.title = "Q3 Forecast.xlsx - Excel"
        self.idle = 0.0
        self.now = 1000.0

    def get_title(self):
        return self.title

    def get_idle(self):
        return self.idle

    def clock(self):
        return self.now


def make_cfg(**kw):
    cfg = Config()
    cfg.metrics_csv = ""          # never write a file in tests
    cfg.use_model = False         # rule-based recap unless a test says otherwise
    cfg.away_s = 10
    cfg.poll_s = 0.01
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def make_engine(desk=None, cfg=None, **kw):
    desk = desk or FakeDesk()
    huds, events, logs = [], [], []
    kw.setdefault("on_event", events.append)
    engine = RecallEngine(cfg or make_cfg(), get_title=desk.get_title, get_idle=desk.get_idle,
                          clock=desk.clock, launch_hud=lambda *a: huds.append(a),
                          log=logs.append, **kw)
    return engine, desk, huds, events, logs


def wait_worker(engine):
    w = engine._worker
    if w is not None:
        w.join(5)


class HelperTests(unittest.TestCase):
    def test_format_away_and_title(self):
        self.assertEqual(format_away(10), "10 s")
        self.assertEqual(format_away(300), "5 min")
        self.assertEqual(build_hud_title(300, Config()), "Welcome back - away 5 min")

    def test_main_reexports_format_away(self):
        from app import main
        self.assertIs(main.format_away, format_away)


class StateMachineTests(unittest.TestCase):
    def test_active_away_back(self):
        engine, desk, huds, events, logs = make_engine()
        engine._running = True                  # drive step() by hand, no thread
        engine.step()
        self.assertEqual(engine.state, State.RUNNING)
        self.assertEqual([e.title for e in engine.snapshot().recent], ["Q3 Forecast.xlsx"])

        desk.idle, desk.now = 12, desk.now + 12
        engine.step()
        self.assertEqual(engine.state, State.AWAY)
        self.assertIn("state: ACTIVE -> AWAY (preparing recap in the background)", logs)
        wait_worker(engine)
        recaps = [e for e in events if isinstance(e, RecapReady)]
        self.assertEqual(len(recaps), 1)
        self.assertEqual(recaps[0].lines[0], "You stopped in Q3 Forecast.xlsx (Excel)")
        self.assertEqual(recaps[0].source, "rule_only")

        desk.title = "Other.docx - Word"         # not logged while away
        desk.idle, desk.now = 30, desk.now + 30
        engine.step()
        self.assertEqual(engine.state, State.AWAY)
        self.assertEqual(len(engine.snapshot().recent), 1)

        desk.idle, desk.now = 0.5, desk.now + 1
        engine.step()
        self.assertEqual(engine.state, State.RUNNING)
        self.assertEqual(len(huds), 1)
        title, lines, seconds = huds[0]
        self.assertEqual(title, "Welcome back - away 43 s")   # away since now-12 when it began
        self.assertEqual(list(lines), list(recaps[0].lines))
        self.assertTrue(any(l.startswith("state: AWAY -> ACTIVE (away 43 s)") for l in logs))
        changes = [(e.old, e.new) for e in events if isinstance(e, StateChanged)]
        self.assertEqual(changes, [(State.RUNNING, State.AWAY), (State.AWAY, State.RUNNING)])

    def test_not_ready_text_when_recap_missing(self):
        engine, desk, huds, events, _ = make_engine(summarize_fn=lambda *a: 1 / 0)
        engine._running = True
        engine.step()
        desk.idle = 20
        engine.step()
        wait_worker(engine)
        self.assertEqual([e.message for e in events if isinstance(e, EngineError)],
                         ["recap failed (ZeroDivisionError)"])
        desk.idle = 0
        engine.step()
        self.assertEqual(list(huds[0][1]), [NOT_READY])

    def test_model_backend_is_injected(self):
        cfg = make_cfg(use_model=True)
        engine, desk, _, events, _ = make_engine(cfg=cfg, backend=MockBackend(cfg))
        engine.observe()
        result = engine.recap_now()
        self.assertEqual(result["source"], "model")
        self.assertEqual(engine.snapshot().latest_recap, tuple(result["bullets"]))
        self.assertEqual(len(engine.snapshot().latest_recap), 3)

    def test_blocked_windows_are_not_logged(self):
        engine, desk, *_ = make_engine()
        desk.title = "My Bank - Online Banking - Chrome"
        self.assertFalse(engine.observe())
        self.assertEqual(engine.snapshot().recent, ())


class PauseTests(unittest.TestCase):
    def test_no_logging_while_paused_and_resume_continues(self):
        engine, desk, _, events, logs = make_engine()
        engine._running = True
        self.assertTrue(engine.pause())
        self.assertFalse(engine.pause())          # already paused
        self.assertTrue(engine.is_paused)
        self.assertEqual(engine.state, State.PAUSED)
        engine.step()
        self.assertEqual(engine.snapshot().recent, ())
        self.assertTrue(engine.resume())
        self.assertFalse(engine.resume())
        engine.step()
        self.assertEqual(len(engine.snapshot().recent), 1)
        changes = [(e.old, e.new) for e in events if isinstance(e, StateChanged)]
        self.assertEqual(changes, [(State.RUNNING, State.PAUSED), (State.PAUSED, State.RUNNING)])

    def test_no_recap_while_paused(self):
        calls = []
        engine, desk, huds, events, _ = make_engine(
            summarize_fn=lambda *a: calls.append(1) or {"bullets": ["x"], "source": "rule_only"})
        engine._running = True
        engine.step()
        engine.pause()
        desk.idle, desk.now = 100, desk.now + 100
        for _ in range(3):
            engine.step()
        self.assertEqual(engine.state, State.PAUSED)
        self.assertEqual(calls, [])
        self.assertIsNone(engine.recap_now())
        self.assertEqual(huds, [])
        self.assertEqual(engine.snapshot().idle_seconds, 0.0)

    def test_paused_time_does_not_count_as_away(self):
        engine, desk, huds, _, _ = make_engine()
        engine._running = True
        engine.step()
        engine.pause()
        desk.idle, desk.now = 600, desk.now + 600   # 10 min paused and untouched
        engine.step()
        engine.resume()
        desk.idle, desk.now = 603, desk.now + 3     # 3 s after resume, raw idle still huge
        engine.step()
        self.assertEqual(engine.state, State.RUNNING)
        self.assertEqual(engine.snapshot().idle_seconds, 3)
        desk.idle, desk.now = 611, desk.now + 8     # 11 s after resume -> away for real
        engine.step()
        self.assertEqual(engine.state, State.AWAY)
        wait_worker(engine)

    def test_pause_while_away_drops_the_recap(self):
        gate = threading.Event()

        def slow(*a):
            gate.wait(5)
            return {"bullets": ["late"], "source": "rule_only"}

        engine, desk, huds, events, _ = make_engine(summarize_fn=slow)
        engine._running = True
        engine.step()
        desk.idle = 20
        engine.step()
        self.assertEqual(engine.state, State.AWAY)
        worker = engine._worker
        engine.pause()
        gate.set()
        worker.join(5)
        self.assertFalse([e for e in events if isinstance(e, RecapReady)])
        self.assertIsNone(engine.snapshot().latest_recap)
        engine.resume()
        desk.idle = 0
        engine.step()
        self.assertEqual(engine.state, State.RUNNING)
        self.assertEqual(huds, [])

    def test_toggle_pause_returns_new_value(self):
        engine, *_ = make_engine()
        self.assertTrue(engine.toggle_pause())
        self.assertFalse(engine.toggle_pause())


class EventAndSnapshotTests(unittest.TestCase):
    def test_failing_callback_does_not_break_the_loop(self):
        def boom(_event):
            raise RuntimeError("listener bug")

        engine, desk, huds, _, logs = make_engine(on_event=boom)
        engine._running = True
        engine.step()
        desk.idle = 20
        engine.step()
        wait_worker(engine)
        desk.idle = 0
        engine.step()
        self.assertEqual(len(huds), 1)
        self.assertTrue(any("event listener failed (RuntimeError)" in l for l in logs))

    def test_step_errors_become_error_events_without_titles(self):
        events = []
        desk = FakeDesk()
        engine = RecallEngine(make_cfg(), get_title=lambda: (_ for _ in ()).throw(OSError("secret.docx")),
                              get_idle=desk.get_idle, clock=desk.clock, launch_hud=lambda *a: None,
                              on_event=events.append)
        engine.start()
        try:
            deadline = time.time() + 3
            while not any(isinstance(e, EngineError) for e in events) and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(engine.is_running)          # loop survives
        finally:
            self.assertTrue(engine.stop())
        errors = [e for e in events if isinstance(e, EngineError)]
        self.assertTrue(errors)
        self.assertEqual(errors[0].message, "watch step failed (OSError)")
        self.assertNotIn("secret", errors[0].message)

    def test_snapshot_is_an_immutable_copy(self):
        engine, desk, *_ = make_engine()
        engine.observe()
        snap = engine.snapshot()
        self.assertIsInstance(snap, Snapshot)
        self.assertIsInstance(snap.recent, tuple)
        self.assertIsInstance(snap.recent[0], WindowEntry)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snap.state = State.RUNNING
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snap.recent[0].title = "changed"
        desk.title = "Second.docx - Word"
        engine.observe()
        self.assertEqual(len(snap.recent), 1)            # old copy unchanged
        self.assertEqual(len(engine.snapshot().recent), 2)
        self.assertEqual(snap.state, State.STOPPED)
        self.assertIsNone(snap.latest_recap)

    def test_snapshot_keeps_at_most_25_titles(self):
        engine, desk, *_ = make_engine()
        for i in range(40):
            desk.title = f"Doc {i}.txt - Notepad"
            engine.observe()
        recent = engine.snapshot().recent
        self.assertEqual(len(recent), 25)
        self.assertEqual(recent[-1].title, "Doc 39.txt")

    def test_snapshot_from_another_thread(self):
        engine, desk, *_ = make_engine()
        engine.start()
        snaps, errors = [], []

        def reader():
            try:
                for _ in range(200):
                    snaps.append(engine.snapshot())
            except Exception as exc:            # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for i in range(50):
            desk.title = f"Doc {i}.txt - Notepad"
            time.sleep(0.001)
        for t in threads:
            t.join(5)
        engine.stop()
        self.assertEqual(errors, [])
        self.assertEqual(len(snaps), 600)

    def test_event_kinds(self):
        self.assertEqual(StateChanged(State.RUNNING, State.AWAY).kind, "state_changed")
        self.assertEqual(RecapReady(("a",), "model").kind, "recap_ready")
        self.assertEqual(EngineError("x").kind, "error")


class ThreadTests(unittest.TestCase):
    def test_start_and_stop_end_the_thread_quickly(self):
        cfg = make_cfg(poll_s=1.0)           # the real poll interval
        engine, desk, _, events, _ = make_engine(cfg=cfg)
        self.assertTrue(engine.start())
        self.assertFalse(engine.start())     # already running
        time.sleep(0.05)
        self.assertTrue(engine.is_running)
        thread = engine._thread
        t0 = time.monotonic()
        self.assertTrue(engine.stop())
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertFalse(thread.is_alive())
        self.assertFalse(engine.is_running)
        self.assertEqual(engine.state, State.STOPPED)
        changes = [(e.old, e.new) for e in events if isinstance(e, StateChanged)]
        self.assertEqual(changes, [(State.STOPPED, State.RUNNING), (State.RUNNING, State.STOPPED)])

    def test_stop_while_waiting_for_a_slow_recap(self):
        gate = threading.Event()
        engine, desk, huds, _, _ = make_engine(
            summarize_fn=lambda *a: gate.wait(10) and {"bullets": ["x"], "source": "rule_only"})
        engine.start()
        desk.idle = 20
        deadline = time.time() + 3
        while engine.state != State.AWAY and time.time() < deadline:
            time.sleep(0.01)
        desk.idle = 0                         # back, but the recap is still running
        time.sleep(0.1)
        t0 = time.monotonic()
        self.assertTrue(engine.stop())
        self.assertLess(time.monotonic() - t0, 1.0)
        gate.set()
        self.assertEqual(huds, [])

    def test_stop_without_start(self):
        engine, *_ = make_engine()
        self.assertTrue(engine.stop())

    def test_pause_before_start_starts_paused(self):
        engine, *_ = make_engine()
        engine.pause()
        engine.start()
        try:
            self.assertEqual(engine.state, State.PAUSED)
        finally:
            engine.stop()


if __name__ == "__main__":
    unittest.main()
