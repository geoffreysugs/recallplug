"""Presenter for the window: tested without Tk."""
import unittest

from app import selfcheck
from app.config import Config
from app.engine import EngineError, RecapReady, Snapshot, State, StateChanged, WindowEntry
from app.gui_model import (BLUE, GREEN, GREY, NO_RECAP, ORANGE, RED, Presenter, engine_label,
                           format_recent, model_label, ollama_label, recap_lines, relative_time)
from app.selfcheck import PullResult, SelfCheckResult


def snap(state=State.RUNNING, recent=(), recap=None):
    return Snapshot(state=state, recent=tuple(recent), latest_recap=recap, idle_seconds=0.0,
                    blocklist_size=14)


def check_result(ollama=selfcheck.RUNNING, model=selfcheck.PRESENT):
    return SelfCheckResult(ollama, model, "qwen2.5:1.5b", "hint text")


class LabelTests(unittest.TestCase):
    def test_engine_state_text_and_colour(self):
        self.assertEqual(engine_label(State.RUNNING), ("Recording", GREEN))
        self.assertEqual(engine_label(State.PAUSED), ("Paused", ORANGE))
        self.assertEqual(engine_label(State.AWAY), ("Away", BLUE))
        self.assertEqual(engine_label(State.STOPPED), ("Stopped", GREY))
        self.assertEqual(engine_label(None), ("Not started", GREY))

    def test_ollama_and_model_labels(self):
        self.assertEqual(ollama_label(None, False), ("Ollama: checking...", GREY))
        self.assertEqual(ollama_label(selfcheck.RUNNING, False), ("Ollama: running", GREEN))
        self.assertEqual(ollama_label(selfcheck.NOT_RUNNING, False), ("Ollama: not running", RED))
        self.assertEqual(ollama_label(selfcheck.NOT_RUNNING, True)[0], "Ollama: not needed")
        self.assertEqual(model_label(selfcheck.PRESENT, "m", False), ("Model: m", GREEN))
        self.assertEqual(model_label(selfcheck.MISSING, "m", False), ("Model: m missing", RED))
        self.assertEqual(model_label(selfcheck.UNKNOWN, "m", False)[1], ORANGE)
        self.assertEqual(model_label(None, "m", True, "mock backend"),
                         ("Model: off (mock backend)", GREY))

    def test_relative_time(self):
        self.assertEqual(relative_time(100, 102), "just now")
        self.assertEqual(relative_time(100, 142), "42 s ago")
        self.assertEqual(relative_time(100, 100 + 185), "3 min ago")
        self.assertEqual(relative_time(100, 100 + 7300), "2 h ago")
        self.assertEqual(relative_time(200, 100), "just now")      # clock skew

    def test_recent_list_is_newest_first_and_limited(self):
        entries = [WindowEntry(1000 + i, f"Doc {i}.txt", "Notepad" if i % 2 else "")
                   for i in range(30)]
        lines = format_recent(entries, now=1029 + 180)
        self.assertEqual(len(lines), 25)
        self.assertTrue(lines[0].endswith("Doc 29.txt (Notepad)"))
        self.assertTrue(lines[0].strip().startswith("3 min ago"))
        self.assertTrue(lines[-1].endswith("Doc 5.txt (Notepad)"))
        self.assertTrue(lines[1].endswith("Doc 28.txt"))

    def test_recap_lines(self):
        self.assertEqual(recap_lines(None), [NO_RECAP])
        self.assertEqual(recap_lines(snap(recap=None)), [NO_RECAP])
        self.assertEqual(recap_lines(snap(recap=("a", "b", "c"))), ["a", "b", "c"])


class QueueTests(unittest.TestCase):
    def make(self, skip=False):
        cfg = Config()
        return Presenter(cfg, skip_check=skip, clock=lambda: 2000.0)

    def test_drain_applies_events_and_returns_actions(self):
        p = self.make()
        p.engine_running()
        p.post("event", StateChanged(State.STOPPED, State.RUNNING))
        p.post("hotkey", "toggle_pause")
        p.post("event", RecapReady(("x",), "model"))
        p.post("event", EngineError("watch step failed (OSError)"))
        actions = p.drain()
        self.assertEqual(actions, ["toggle_pause"])
        self.assertEqual(p.engine_state, State.RUNNING)
        self.assertEqual((p.message, p.message_color), ("Error: watch step failed (OSError)", RED))
        self.assertTrue(p.queue.empty())

    def test_drain_respects_max_items(self):
        p = self.make()
        for _ in range(5):
            p.post("hotkey", "toggle_pause")
        self.assertEqual(len(p.drain(max_items=3)), 3)
        self.assertEqual(len(p.drain()), 2)

    def test_selfcheck_ok_starts_engine_once(self):
        p = self.make()
        p.start_check()
        p.post("selfcheck", check_result())
        self.assertEqual(p.drain(), ["start_engine"])
        p.engine_running()
        p.post("selfcheck", check_result())
        self.assertEqual(p.drain(), [])

    def test_selfcheck_problems_do_not_start_engine(self):
        for ollama, model in ((selfcheck.NOT_RUNNING, selfcheck.UNKNOWN),
                              (selfcheck.RUNNING, selfcheck.MISSING)):
            p = self.make()
            p.post("selfcheck", check_result(ollama, model))
            self.assertEqual(p.drain(), [])
            self.assertFalse(p.can_start_engine())

    def test_skip_check_can_always_start(self):
        self.assertTrue(self.make(skip=True).can_start_engine())

    def test_download_progress_and_done(self):
        p = self.make()
        p.start_download()
        p.post("pull_progress", 42.4, "pulling abc")
        p.drain()
        self.assertEqual(p.download_percent, 42.4)
        self.assertIn("42%", p.message)
        p.post("pull_done", PullResult(True, "qwen2.5:1.5b is installed."))
        self.assertEqual(p.drain(), ["recheck"])
        self.assertFalse(p.downloading)
        p.start_download()
        p.post("pull_done", PullResult(False, "Download failed: x"))
        self.assertEqual(p.drain(), [])
        self.assertEqual(p.message_color, RED)

    def test_snapshot_feeds_list_and_recap(self):
        p = self.make()
        p.set_snapshot(snap(recent=[WindowEntry(1990, "A.txt", "Notepad")], recap=("one",)))
        self.assertEqual(p.recent_lines(), ["  10 s ago   A.txt (Notepad)"])
        self.assertEqual(p.recap(), ["one"])


class ButtonRuleTests(unittest.TestCase):
    def test_before_check(self):
        p = Presenter(Config(), skip_check=False)
        p.start_check()
        b = p.buttons()
        self.assertFalse(b["pause"].enabled)
        self.assertFalse(b["export"].enabled)
        self.assertFalse(b["reload_blocklist"].enabled)
        self.assertTrue(b["settings"].enabled and b["edit_blocklist"].enabled)
        for name in ("check_again", "download", "open_ollama"):
            self.assertFalse(b[name].visible, name)

    def test_ollama_down(self):
        p = Presenter(Config(), skip_check=False)
        p.post("selfcheck", check_result(selfcheck.NOT_RUNNING, selfcheck.UNKNOWN))
        p.drain()
        b = p.buttons()
        self.assertTrue(b["open_ollama"].visible)
        self.assertTrue(b["check_again"].visible and b["check_again"].enabled)
        self.assertFalse(b["download"].visible)

    def test_model_missing_and_downloading(self):
        p = Presenter(Config(), skip_check=False)
        p.post("selfcheck", check_result(selfcheck.RUNNING, selfcheck.MISSING))
        p.drain()
        b = p.buttons()
        self.assertTrue(b["download"].visible and b["download"].enabled)
        self.assertFalse(b["open_ollama"].visible)
        p.start_download()
        b = p.buttons()
        self.assertFalse(b["download"].enabled)
        self.assertFalse(b["check_again"].enabled)

    def test_running_paused_and_data(self):
        p = Presenter(Config(), skip_check=True)
        p.engine_running()
        p.set_snapshot(snap(State.RUNNING))
        b = p.buttons()
        self.assertEqual((b["pause"].enabled, b["pause"].text), (True, "Pause"))
        self.assertFalse(b["export"].enabled)
        self.assertTrue(b["reload_blocklist"].enabled)
        for name in ("check_again", "download", "open_ollama"):
            self.assertFalse(b[name].visible, name)
        p.set_snapshot(snap(State.PAUSED, recent=[WindowEntry(1, "A", "")]))
        b = p.buttons()
        self.assertEqual(b["pause"].text, "Resume")
        self.assertTrue(b["export"].enabled)

    def test_status_labels_follow_engine(self):
        p = Presenter(Config(), skip_check=True, skip_reason="mock backend")
        self.assertEqual(p.status_labels()["engine"][0], "Not started")
        p.engine_running()
        p.set_snapshot(snap(State.AWAY))
        labels = p.status_labels()
        self.assertEqual(labels["engine"], ("Away", BLUE))
        self.assertEqual(labels["model"][0], "Model: off (mock backend)")


class AskStateTests(unittest.TestCase):
    def setUp(self):
        from app.ask import AskResult
        self.AskResult = AskResult
        self.p = Presenter(Config(), skip_check=True)
        self.p.engine_running()

    def test_ask_enabled_rules(self):
        p = Presenter(Config(), skip_check=True)
        self.assertFalse(p.ask_enabled("Which files?"))          # engine not started
        self.assertFalse(self.p.ask_enabled("   "))
        self.assertTrue(self.p.ask_enabled("Which files?"))
        self.p.start_ask()
        self.assertFalse(self.p.ask_enabled("Which files?"))     # busy

    def test_thinking_then_answer(self):
        ask_id = self.p.start_ask()
        self.assertEqual(self.p.ask_answer, "Thinking...")
        self.p.post("ask_done", ask_id, self.AskResult("It was [1].", "model", (1,)))
        self.p.drain()
        self.assertEqual((self.p.ask_answer, self.p.ask_source, self.p.ask_busy),
                         ("It was [1].", "model", False))

    def test_timeout_and_late_answer_is_ignored(self):
        from app.gui_model import ASK_TIMEOUT_TEXT
        ask_id = self.p.start_ask()
        self.assertTrue(self.p.ask_timeout(ask_id))
        self.assertEqual(self.p.ask_answer, ASK_TIMEOUT_TEXT)
        self.assertFalse(self.p.ask_busy)
        self.p.post("ask_done", ask_id, self.AskResult("late", "model", (1,)))
        self.p.drain()
        self.assertEqual(self.p.ask_answer, ASK_TIMEOUT_TEXT)
        self.assertFalse(self.p.ask_timeout(ask_id))             # only once

    def test_timer_of_an_old_request_does_nothing(self):
        first = self.p.start_ask()
        self.p.post("ask_done", first, self.AskResult("a [1].", "model", (1,)))
        self.p.drain()
        second = self.p.start_ask()
        self.assertFalse(self.p.ask_timeout(first))
        self.assertTrue(self.p.ask_busy)
        self.p.post("ask_done", second, self.AskResult("b [1].", "model", (1,)))
        self.p.drain()
        self.assertEqual(self.p.ask_answer, "b [1].")


if __name__ == "__main__":
    unittest.main()
