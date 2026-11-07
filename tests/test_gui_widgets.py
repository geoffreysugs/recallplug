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
            def __init__(self, cb, vk=None, hotkey_id=None):
                self.cb, self.vk, self.id, self.stopped = cb, vk, hotkey_id, False
                made.append(self)

            def start(self):
                return True

            def stop(self):
                self.stopped = True

        beeps, exports = [], []
        app = self.App(self.root, self.cfg, skip_check=True, engine_factory=self.factory,
                       hotkey_factory=FakeHotkey, beep=beeps.append, poll_ms=50, drain_ms=20)
        app.on_export = lambda: exports.append(threading.current_thread().name)
        app.start()
        self.assertEqual([(h.vk, h.id) for h in made], [(ord("P"), 1), (ord("E"), 2)])
        t = threading.Thread(target=made[0].cb)       # "hotkey thread"
        t.start()
        t.join()
        self.assertFalse(app.engine.is_paused)        # nothing happens off the Tk thread
        self.assertTrue(pump(self.root, 2, lambda: app.engine.is_paused))
        self.assertTrue(pump(self.root, 1, lambda: beeps == [True]))
        t = threading.Thread(target=made[1].cb)       # Ctrl+Alt+E
        t.start()
        t.join()
        self.assertEqual(exports, [])
        self.assertTrue(pump(self.root, 2, lambda: exports == ["MainThread"]))
        app.close()
        self.assertTrue(all(h.stopped for h in made))

    def test_failed_hotkeys_are_reported(self):
        class Broken:
            win32_error = 1409

            def __init__(self, cb, **kw):
                pass

            def start(self):
                return False

        app = self.App(self.root, self.cfg, skip_check=True, engine_factory=self.factory,
                       hotkey_factory=Broken, poll_ms=50, drain_ms=20)
        app.start()
        self.assertIn("Ctrl+Alt+P (Win32 error 1409)", app.presenter.message)
        self.assertIn("Ctrl+Alt+E (Win32 error 1409)", app.presenter.message)
        self.assertTrue(app.engine.is_running)
        app.close()

    def _session(self, **kw):
        app = self.App(self.root, self.cfg, skip_check=True, engine_factory=self.factory,
                       hotkey_factory=None, poll_ms=50, drain_ms=20, **kw)
        app.start()
        pump(self.root, 2, lambda: app.recent_list.size() == 3)
        return app

    def test_export_needs_confirmation(self):
        import tempfile
        from pathlib import Path
        from app.export import WARNING
        asked = []
        answer = [False]

        def confirm(title, text):
            asked.append(text)
            return answer[0]

        with tempfile.TemporaryDirectory() as tmp:
            app = self._session(confirm_fn=confirm, export_dir=Path(tmp))
            self.assertEqual(str(app.buttons["export"].cget("state")), "normal")
            app.on_export()
            self.assertEqual(asked, [WARNING])
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(app.presenter.message, "Export cancelled.")
            answer[0] = True
            app.on_export()
            files = list(Path(tmp).iterdir())
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r"^recallplug_\d{8}_\d{6}\.txt$")
            self.assertIn("Contract Draft v2.txt (Notepad)", files[0].read_text(encoding="utf-8"))
            self.assertIn(str(files[0]), app.presenter.message)
            app.close()

    def test_edit_and_reload_blocklist(self):
        import tempfile
        from pathlib import Path
        from app import blocklist
        opened = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blocklist.txt"
            self.cfg.metrics_csv = ""
            app = self.App(self.root, self.cfg, skip_check=True, hotkey_factory=None,
                           blocklist_path=path, blocklist_loader=blocklist.make_loader(path),
                           open_file=opened.append, poll_ms=50, drain_ms=20,
                           engine_factory=lambda on_event: RecallEngine(
                               self.cfg, get_title=self.fake.get_title,
                               get_idle=self.fake.get_idle, launch_hud=lambda *a: None,
                               on_event=on_event, blocklist_loader=blocklist.make_loader(path)))
            app.on_edit_blocklist()
            self.assertEqual(opened, [str(path)])
            self.assertTrue(path.exists())                 # created from the defaults
            app.start()
            path.write_text("bank\nnotes\n", encoding="utf-8")
            app.on_reload_blocklist()
            self.assertEqual(app.presenter.message, "Block list reloaded: 2 entries.")
            app.close()

    def test_settings_dialog_validates_and_saves(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            app = self._session(settings_path=path)
            app.on_settings()
            dialog = app.settings_window
            dialog.window.withdraw()
            self.assertIn("next time", dialog.NOTE)
            dialog.away.set("0")
            self.assertFalse(dialog.save())
            self.assertIn("1 to 60", dialog.error.cget("text"))
            self.assertFalse(path.exists())
            dialog.away.set("15")
            dialog.demo.set(True)
            dialog.model.set("qwen2.5:3b")
            self.assertTrue(dialog.save())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"away_minutes": 15, "demo": True, "model": "qwen2.5:3b"})
            self.assertIsNone(app.settings_window)
            self.assertIn("next start", app.presenter.message)
            app.close()


    def test_ask_runs_in_a_worker_and_shows_the_answer(self):
        from app.ask import AskResult
        seen = []

        def fake_ask(question, snapshot, backend, cfg):
            seen.append((question, threading.current_thread().name, len(snapshot.recent)))
            return AskResult("You had Contract Draft v2.txt open [1].", "model", (1,))

        app = self.App(self.root, self.cfg, skip_check=True, engine_factory=self.factory,
                       hotkey_factory=None, ask_fn=fake_ask, poll_ms=50, drain_ms=20)
        app.start()
        pump(self.root, 2, lambda: app.recent_list.size() == 3)
        self.assertEqual(str(app.ask_button.cget("state")), "disabled")     # empty question
        app.question_var.set("Which contract file did I have open?")
        app.render()
        self.assertEqual(str(app.ask_button.cget("state")), "normal")
        app.on_ask()
        self.assertTrue(pump(self.root, 3, lambda: "Contract Draft" in
                             app.answer_text.get("1.0", "end")))
        self.assertEqual(seen[0][1], "RecallPlug-ask")
        self.assertEqual(seen[0][2], 3)
        labels = [w.cget("text") for w in app.ask_entry.master.master.winfo_children()
                  if w.winfo_class() == "Label"]
        self.assertIn("AI answer, may be wrong", labels)
        app.close()

    def test_ask_timeout_message(self):
        from app.ask import AskResult
        gate = threading.Event()

        def slow_ask(*a):
            gate.wait(5)
            return AskResult("late [1].", "model", (1,))

        app = self.App(self.root, self.cfg, skip_check=True, engine_factory=self.factory,
                       hotkey_factory=None, ask_fn=slow_ask, ask_timeout_ms=200,
                       poll_ms=50, drain_ms=20)
        app.start()
        pump(self.root, 2, lambda: app.recent_list.size() >= 1)
        app.question_var.set("Did I read the notes?")
        app.on_ask()
        self.assertIn("Thinking...", app.answer_text.get("1.0", "end"))
        self.assertTrue(pump(self.root, 3, lambda: "took too long" in app.answer_text.get("1.0", "end")))
        gate.set()
        pump(self.root, 0.3)
        self.assertIn("took too long", app.answer_text.get("1.0", "end"))
        app.close()


if __name__ == "__main__":
    unittest.main()
