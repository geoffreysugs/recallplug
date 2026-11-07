"""Pause hotkey with a fake Win32 wrapper (no real hotkey is ever registered)."""
import contextlib
import io
import queue
import threading
import time
import unittest
from unittest import mock

from app import hotkey
from app import main as app_main
from app.config import Config
from app.engine import RecallEngine

REAL_SLEEP = time.sleep


class FakeApi:
    def __init__(self, register_ok=True):
        self.register_ok = register_ok
        self.queue = queue.Queue()
        self.registered = []
        self.unregistered = []
        self.threads = set()

    def thread_id(self):
        self.threads.add(threading.get_ident())
        return 4242

    def ensure_queue(self):
        pass

    def register(self, hotkey_id, modifiers, vk):
        self.registered.append((hotkey_id, modifiers, vk))
        return self.register_ok

    def unregister(self, hotkey_id):
        self.unregistered.append(hotkey_id)

    def get_message(self):
        return self.queue.get(timeout=5)

    def post_quit(self, thread_id):
        assert thread_id == 4242
        self.queue.put(None)
        return True

    def press(self, hotkey_id=1):
        self.queue.put((hotkey.WM_HOTKEY, hotkey_id))


def wait_for(cond, timeout=3):
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        time.sleep(0.005)
    return cond()


class GlobalHotkeyTests(unittest.TestCase):
    def test_registers_ctrl_alt_p_and_calls_back(self):
        api, calls = FakeApi(), []
        hk = hotkey.GlobalHotkey(lambda: calls.append(1), api_factory=lambda: api)
        self.assertTrue(hk.start())
        self.assertEqual(api.registered,
                         [(1, hotkey.MOD_CONTROL | hotkey.MOD_ALT | hotkey.MOD_NOREPEAT, ord("P"))])
        self.assertNotIn(threading.get_ident(), api.threads)    # its own thread
        api.press()
        api.queue.put((0x0100, 0))                              # other messages are ignored
        api.press(hotkey_id=7)                                  # not our id
        api.press()
        self.assertTrue(wait_for(lambda: len(calls) == 2))
        hk.stop()
        self.assertFalse(hk._thread.is_alive())
        self.assertEqual(api.unregistered, [1])
        self.assertEqual(len(calls), 2)

    def test_failing_callback_keeps_listening(self):
        api, calls = FakeApi(), []

        def cb():
            calls.append(1)
            raise RuntimeError("boom")
        hk = hotkey.GlobalHotkey(cb, api_factory=lambda: api)
        hk.start()
        api.press()
        api.press()
        self.assertTrue(wait_for(lambda: len(calls) == 2))
        hk.stop()

    def test_registration_failure_returns_false(self):
        api = FakeApi(register_ok=False)
        hk = hotkey.GlobalHotkey(lambda: None, api_factory=lambda: api)
        self.assertFalse(hk.start())
        self.assertTrue(wait_for(lambda: not hk._thread.is_alive()))
        hk.stop()                                              # harmless

    def test_api_that_cannot_load_returns_false(self):
        def broken():
            raise OSError("no user32")
        hk = hotkey.GlobalHotkey(lambda: None, api_factory=broken)
        self.assertFalse(hk.start())
        self.assertEqual(hk.error, "OSError")


class MainHotkeyTests(unittest.TestCase):
    def make_engine(self):
        cfg = Config()
        cfg.metrics_csv = ""
        return RecallEngine(cfg, get_title=lambda: "", get_idle=lambda: 0.0,
                            launch_hud=lambda *a: None)

    def test_hotkey_toggles_pause_and_beeps(self):
        engine, api, beeps = self.make_engine(), FakeApi(), []
        factory = lambda cb: hotkey.GlobalHotkey(cb, api_factory=lambda: api)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hk = app_main.start_pause_hotkey(engine, hotkey_factory=factory, beep=beeps.append)
        self.assertIsNotNone(hk)
        self.assertIn("Ctrl+Alt+P", out.getvalue())
        api.press()
        self.assertTrue(wait_for(lambda: beeps == [True]))
        self.assertTrue(engine.is_paused)
        api.press()
        self.assertTrue(wait_for(lambda: beeps == [True, False]))
        self.assertFalse(engine.is_paused)
        hk.stop()

    def test_registration_failure_prints_a_warning_and_continues(self):
        engine = self.make_engine()
        factory = lambda cb: hotkey.GlobalHotkey(cb, api_factory=lambda: FakeApi(register_ok=False))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hk = app_main.start_pause_hotkey(engine, hotkey_factory=factory, beep=lambda p: None)
        self.assertIsNone(hk)
        self.assertEqual(out.getvalue().count("warning:"), 1)

    def test_run_live_starts_and_stops_everything(self):
        """run_live with a fake engine loop: Ctrl+C stops the engine and the hotkey."""
        cfg = Config()
        cfg.metrics_csv = ""
        cfg.poll_s = 0.01
        api = FakeApi()
        made = {}

        def fake_make_engine(cfg, debug=False, blocklist_loader=None):
            made["engine"] = RecallEngine(cfg, get_title=lambda: "Doc.txt - Notepad",
                                          get_idle=lambda: 0.0, launch_hud=lambda *a: None,
                                          log=app_main.say)
            return made["engine"]

        sleeps = []

        def fake_sleep(s):                       # the main thread's wait loop
            sleeps.append(s)
            if len(sleeps) >= 3:
                raise KeyboardInterrupt          # like Ctrl+C
            REAL_SLEEP(0.05)

        factory = lambda cb: hotkey.GlobalHotkey(cb, api_factory=lambda: api)
        orig = app_main.start_pause_hotkey
        with mock.patch.object(app_main, "make_engine", fake_make_engine), \
             mock.patch.object(app_main, "start_pause_hotkey",
                               lambda e: orig(e, hotkey_factory=factory, beep=lambda p: None)), \
             mock.patch.object(app_main.time, "sleep", fake_sleep), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(KeyboardInterrupt):
                app_main.run_live(cfg)
        engine = made["engine"]
        self.assertFalse(engine.is_running)
        self.assertEqual(api.unregistered, [1])
        text = out.getvalue()
        self.assertIn("RecallPlug running. Backend: ollama, model: qwen2.5:1.5b.", text)
        self.assertIn("logged: Doc.txt  (Notepad)", text)


if __name__ == "__main__":
    unittest.main()
