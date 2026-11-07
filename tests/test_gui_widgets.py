"""Widget smoke tests: a withdrawn (never shown) Tk window with fake collaborators.
Skipped when Tk cannot start (for example on a machine without a display)."""
import threading
import time
import unittest

from app import selfcheck
from app.config import Config
from app.engine import RecallEngine, State

try:
    import tkinter as tk
except Exception:          # pragma: no cover
    tk = None


def make_root():
    if tk is None:
        raise unittest.SkipTest("tkinter is not available")
    try:
        root = tk.Tk()
    except Exception as exc:
        raise unittest.SkipTest(f"Tk cannot start: {exc}")
    root.withdraw()
    return root


def pump(root, seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if until and until():
            return True
        time.sleep(0.01)
    return bool(until and until())


class FakeProviders:
    titles = ["Supplier Quotes.txt - Notepad", "Meeting Notes.txt - Notepad",
              "Contract Draft v2.txt - Notepad"]

    def __init__(self):
        self.i = 0
        self.idle = 0.0

    def get_title(self):
        self.i += 1
        return self.titles[min(self.i // 2, 2)]

    def get_idle(self):
        return self.idle


class WindowTests(unittest.TestCase):
    def setUp(self):
        from app.gui import RecallPlugApp
        self.root = make_root()
        self.cfg = Config()
        self.cfg.metrics_csv = ""
        self.cfg.use_model = False
        self.cfg.poll_s = 0.02
        self.cfg.away_s = 1
        self.fake = FakeProviders()
        self.huds = []

        def factory(on_event):
            return RecallEngine(self.cfg, get_title=self.fake.get_title,
                                get_idle=self.fake.get_idle,
                                launch_hud=lambda *a: self.huds.append(a), on_event=on_event)
        self.App = RecallPlugApp
        self.factory = factory

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_fake_session_titles_pause_recap_close(self):
        app = self.App(self.root, self.cfg, skip_check=True, skip_reason="--no-model",
                       engine_factory=self.factory, hotkey_factory=None, poll_ms=50, drain_ms=20)
        app.start()
        self.assertEqual(self.root.title(), "Recall Plug")
        self.assertTrue(pump(self.root, 3, lambda: app.recent_list.size() == 3))
        self.assertTrue(app.recent_list.get(0).endswith("Contract Draft v2.txt (Notepad)"))
        self.assertEqual(app.status_labels["engine"].cget("text"), "Recording")
        app.on_pause()
        self.assertTrue(app.engine.is_paused)
        self.assertEqual(str(app.buttons["pause"].cget("text")), "Resume")
        self.assertEqual(app.status_labels["engine"].cget("text"), "Paused")
        app.on_pause()
        self.assertFalse(app.engine.is_paused)
        self.fake.idle = 5.0
        self.assertTrue(pump(self.root, 5, lambda: "You stopped in" in app.recap_label.cget("text")))
        self.fake.idle = 0.0
        self.assertTrue(pump(self.root, 3, lambda: bool(self.huds)))
        engine = app.engine
        app.close()
        self.assertTrue(app.closed)
        self.assertFalse(engine.is_running)
        self.assertEqual(app.hotkeys, [])

    def test_ollama_down_shows_help_buttons_and_no_engine(self):
        opened = []
        down = selfcheck.SelfCheckResult(selfcheck.NOT_RUNNING, selfcheck.UNKNOWN, "m", "Ollama is down.")
        app = self.App(self.root, self.cfg, skip_check=False, engine_factory=self.factory,
                       check_fn=lambda cfg: down, hotkey_factory=None, open_url=opened.append,
                       poll_ms=50, drain_ms=20)
        app.start()
        self.assertTrue(pump(self.root, 2, lambda: app.presenter.ollama_state is not None))
        pump(self.root, 0.1)
        self.assertIsNone(app.engine)
        self.assertTrue(app.buttons["open_ollama"].winfo_manager())       # gridded
        self.assertFalse(app.buttons["download"].winfo_manager())
        app.on_open_ollama()
        self.assertEqual(opened, ["https://ollama.com/download"])
        self.assertEqual(app.status_labels["ollama"].cget("text"), "Ollama: not running")
        app.close()

    def test_model_download_then_engine_starts(self):
        states = iter([
            selfcheck.SelfCheckResult(selfcheck.RUNNING, selfcheck.MISSING, "m", "missing"),
            selfcheck.SelfCheckResult(selfcheck.RUNNING, selfcheck.PRESENT, "m", "ok"),
        ])
        seen = []

        def pull(cfg, cb):
            for pct in (0.0, 50.0, 100.0):
                cb(pct, "pulling abc")
            seen.append(threading.current_thread().name)
            return selfcheck.PullResult(True, "m is installed.")

        app = self.App(self.root, self.cfg, skip_check=False, engine_factory=self.factory,
                       check_fn=lambda cfg: next(states), pull_fn=pull, hotkey_factory=None,
                       poll_ms=50, drain_ms=20)
        app.start()
        self.assertTrue(pump(self.root, 2, lambda: app.buttons["download"].winfo_manager() != ""))
        app.on_download()
        self.assertTrue(pump(self.root, 3, lambda: app.engine is not None))
        self.assertEqual(seen, ["RecallPlug-pull"])
        self.assertEqual(app.engine.state, State.RUNNING)
        app.close()

    def test_hotkey_callback_goes_through_the_queue(self):
        made = []

        class FakeHotkey:
            def __init__(self, cb):
                self.cb, self.stopped = cb, False
                made.append(self)

            def start(self):
                return True

            def stop(self):
                self.stopped = True

        beeps = []
        app = self.App(self.root, self.cfg, skip_check=True, engine_factory=self.factory,
                       hotkey_factory=FakeHotkey, beep=beeps.append, poll_ms=50, drain_ms=20)
        app.start()
        t = threading.Thread(target=made[0].cb)       # "hotkey thread"
        t.start()
        t.join()
        self.assertFalse(app.engine.is_paused)        # nothing happens off the Tk thread
        self.assertTrue(pump(self.root, 2, lambda: app.engine.is_paused))
        self.assertTrue(pump(self.root, 1, lambda: beeps == [True]))
        app.close()
        self.assertTrue(made[0].stopped)


if __name__ == "__main__":
    unittest.main()
