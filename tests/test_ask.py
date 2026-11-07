"""Ask: router, list answers, model prompt, post-check. Fake backends only."""
import unittest
from unittest import mock

from app import ask as ask_mod
from app.ask import (DONT_KNOW, MODEL_OFF, NO_WINDOWS, REFUSAL, AskResult, ask, build_prompt,
                     check_answer, route)
from app.config import Config
from app.engine import Snapshot, State, WindowEntry

NOW = 10_000.0


def snap(*titles):
    """titles oldest first: (title, app, minutes_ago)."""
    recent = tuple(WindowEntry(NOW - m * 60, t, a) for t, a, m in titles)
    return Snapshot(State.RUNNING, recent, None, 0.0, 14)


THREE = snap(("Supplier Quotes.txt", "Notepad", 9), ("Meeting Notes.txt", "Notepad", 6),
             ("Contract Draft v2.txt", "Notepad", 3))


class FakeBackend:
    name = "fake"

    def __init__(self, answer="", error=None):
        self.answer, self.error = answer, error
        self.prompts, self.kwargs = [], []

    def generate_text(self, prompt, temperature=0.1, num_predict=None):
        self.prompts.append(prompt)
        self.kwargs.append({"temperature": temperature, "num_predict": num_predict})
        if self.error:
            raise self.error
        return self.answer, {}

    def health(self):
        return True


class PlainBackend:
    """A backend whose generate_text takes only the prompt."""
    def __init__(self):
        self.prompts = []

    def generate_text(self, prompt):
        self.prompts.append(prompt)
        return "It was Meeting Notes.txt [2].", {}


def cfg(**kw):
    c = Config()
    c.metrics_csv = ""
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class RouterTests(unittest.TestCase):
    def test_list_questions_are_routed(self):
        cases = {
            "Which documents did I open?": "list",
            "what files did I use": "list",
            "Which windows did I look at?": "list",
            "Which pages were open?": "list",
            "What was I doing?": "list",
            "What have I been working on?": "list",
            "Show me my recent windows": "list",
            "What apps did I use?": "apps",
            "Which applications did I have open?": "apps",
            "What was the last window?": "last",
            "Where did I stop?": "last",
            "How many windows did I open?": "count",
        }
        for q, kind in cases.items():
            self.assertEqual(route(q), kind, q)

    def test_other_questions_go_to_the_model(self):
        for q in ("Which contract file did I have open?", "What is the capital of France?",
                  "What was the total price in the supplier quote?", "Did I read the notes?"):
            self.assertIsNone(route(q), q)

    def test_list_answer_is_newest_first_with_relative_times(self):
        backend = FakeBackend()
        r = ask("Which files did I open?", THREE, backend, cfg(), now=NOW)
        self.assertEqual(r.source, "list")
        lines = r.text.splitlines()
        self.assertEqual(lines[1], "1. Contract Draft v2.txt (Notepad), 3 min ago")
        self.assertEqual(lines[3], "3. Supplier Quotes.txt (Notepad), 9 min ago")
        self.assertEqual(r.cited, (1, 2, 3))
        self.assertEqual(backend.prompts, [])                      # no model call

    def test_last_count_and_apps(self):
        s = snap(("A.txt", "Notepad", 70), ("B.xlsx", "Excel", 2), ("A.txt", "Notepad", 0.2))
        self.assertEqual(ask("What was the last window?", s, None, cfg(), now=NOW).text,
                         "The last window was A.txt (Notepad), just now [1].")
        self.assertTrue(ask("How many windows?", s, None, cfg(), now=NOW).text
                        .startswith("2 different windows"))
        apps = ask("Which apps did I use?", s, None, cfg(), now=NOW).text
        self.assertIn("Notepad: [1], [3]", apps)
        self.assertIn("Excel: [2]", apps)
        self.assertIn("1 h ago", ask("list my windows", s, None, cfg(), now=NOW).text)

    def test_empty_list(self):
        empty = Snapshot(State.RUNNING, (), None, 0.0, 14)
        backend = FakeBackend("x [1].")
        for q in ("Which files did I open?", "What was the last window?",
                  "Which contract file did I have open?"):
            self.assertEqual(ask(q, empty, backend, cfg(), now=NOW),
                             AskResult(NO_WINDOWS, "list"), q)
        self.assertEqual(backend.prompts, [])


class ModelPathTests(unittest.TestCase):
    def test_prompt_holds_only_the_list_and_the_question(self):
        backend = FakeBackend("You had Contract Draft v2.txt open [1].")
        r = ask("Which contract file did I have open?", THREE, backend, cfg(), now=NOW)
        self.assertEqual(r, AskResult("You had Contract Draft v2.txt open [1].", "model", (1,)))
        prompt = backend.prompts[0]
        self.assertIn("1. Contract Draft v2.txt (Notepad), 3 min ago\n2. Meeting Notes.txt", prompt)
        self.assertIn("Question: Which contract file did I have open?\nAnswer:", prompt)
        self.assertIn(DONT_KNOW, prompt)
        self.assertIn(REFUSAL, prompt)
        self.assertEqual(backend.kwargs, [{"temperature": 0.0, "num_predict": ask_mod.ASK_NUM_PREDICT}])

    def test_masking_is_applied_to_titles_and_question(self):
        s = snap(("Mail from amy@example.com - Outlook", "", 1), ("Call 0912-345-678", "", 0.5))
        backend = FakeBackend("I don't know from the recent windows.")
        ask("Did amy@example.com write back?", s, backend, cfg(), now=NOW)
        prompt = backend.prompts[0]
        self.assertNotIn("amy@example.com", prompt)
        self.assertNotIn("0912-345-678", prompt)
        self.assertIn("[EMAIL]", prompt)
        self.assertIn("[PHONE]", prompt)

    def test_backend_with_plain_signature(self):
        backend = PlainBackend()
        r = ask("Did I read the notes?", THREE, backend, cfg(), now=NOW)
        self.assertEqual((r.source, r.cited), ("model", (2,)))

    def test_backend_error_is_a_short_message(self):
        backend = FakeBackend(error=OSError("Contract Draft v2.txt secret"))
        r = ask("Did I read the notes?", THREE, backend, cfg(), now=NOW)
        self.assertEqual(r.source, "unknown")
        self.assertIn("(OSError)", r.text)
        self.assertNotIn("Contract", r.text)
        self.assertNotIn("Traceback", r.text)

    def test_no_model_mode(self):
        backend = FakeBackend("x [1].")
        r = ask("Did I read the notes?", THREE, backend, cfg(use_model=False), now=NOW)
        self.assertEqual(r, AskResult(MODEL_OFF, "unknown"))
        self.assertEqual(backend.prompts, [])
        self.assertEqual(ask("Which files did I open?", THREE, None, cfg(use_model=False),
                             now=NOW).source, "list")

    def test_default_backend_comes_from_config_without_network(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            r = ask("Did I read the notes?", THREE, None, cfg(backend="mock"), now=NOW)
        self.assertIn(r.source, ("model", "unknown"))

    def test_nothing_is_written_to_disk(self):
        backend = FakeBackend("You read Meeting Notes.txt [2].")
        with mock.patch("builtins.open", side_effect=AssertionError("file write")), \
             mock.patch("pathlib.Path.write_text", side_effect=AssertionError("file write")), \
             mock.patch("os.open", side_effect=AssertionError("file write")):
            for q in ("Did I read the notes?", "Which files did I open?"):
                ask(q, THREE, backend, cfg(), now=NOW)

    def test_empty_question(self):
        self.assertEqual(ask("  ", THREE, FakeBackend(), cfg(), now=NOW).source, "unknown")


class PostCheckTests(unittest.TestCase):
    def test_uncited_answer_becomes_dont_know(self):
        self.assertEqual(check_answer("The total was 500 dollars.", 3), AskResult(DONT_KNOW, "unknown"))

    def test_invalid_item_number_becomes_dont_know(self):
        self.assertEqual(check_answer("It was in item [7].", 3), AskResult(DONT_KNOW, "unknown"))
        self.assertEqual(check_answer("See [0].", 3), AskResult(DONT_KNOW, "unknown"))

    def test_other_citation_styles(self):
        self.assertEqual(check_answer("Meeting Notes (item 2) was open.", 3).cited, (2,))
        self.assertEqual(check_answer("Both [1] and [3], not [9].", 3).cited, (1, 3))

    def test_refusal_and_dont_know_sentences(self):
        self.assertEqual(check_answer("I can only answer questions about your recent windows.", 3),
                         AskResult(REFUSAL, "refused"))
        self.assertEqual(check_answer("I don’t know from the recent windows [2].", 3),
                         AskResult(DONT_KNOW, "unknown"))
        self.assertEqual(check_answer("", 3), AskResult(DONT_KNOW, "unknown"))

    def test_more_than_three_sentences_are_cut(self):
        r = check_answer("One [1]. Two [2]. Three [3]. Four [1]. Five.", 3)
        self.assertEqual(r.text, "One [1]. Two [2]. Three [3].")
        self.assertEqual(r.source, "model")

    def test_file_names_with_dots_are_not_split(self):
        r = check_answer("You had Contract Draft v2.txt open [1]. Then Notes.txt [2].", 3)
        self.assertEqual(r.text, "You had Contract Draft v2.txt open [1]. Then Notes.txt [2].")

    def test_uncited_answer_naming_a_full_title_is_cited(self):
        names = ["Contract Draft v2.txt", "Meeting Notes.txt", "Supplier Quotes.txt"]
        r = check_answer("You had the Contract Draft v2.txt open.", 3, names)
        self.assertEqual(r, AskResult("You had the Contract Draft v2.txt open. [1]", "model", (1,)))
        self.assertEqual(check_answer("You had a contract open.", 3, names).source, "unknown")
        self.assertEqual(check_answer("See item [9] Contract Draft v2.txt.", 3, names).source,
                         "unknown")                           # an invalid citation is not repaired

    def test_relatedness_picks_refusal_or_dont_know(self):
        from app.ask import related
        names = ["Contract Draft v2.txt", "Meeting Notes.txt", "Supplier Quotes.txt"]
        self.assertTrue(related("What was the total price in the supplier quote?", names))
        self.assertTrue(related("Did I finish it?", names))
        self.assertFalse(related("What is the capital of France?", names))
        self.assertEqual(check_answer("I can only answer questions about your recent windows.", 3,
                                      names, "What was the total price in the supplier quote?"),
                         AskResult(DONT_KNOW, "unknown"))
        self.assertEqual(check_answer("I don't know from the recent windows.", 3, names,
                                      "What is the capital of France?"),
                         AskResult(REFUSAL, "refused"))
        self.assertEqual(check_answer("Paris.", 3, names, "What is the capital of France?"),
                         AskResult(REFUSAL, "refused"))
        self.assertEqual(check_answer("You read it [2].", 3, names, "What is the capital of France?").source,
                         "model")                            # a cited answer is kept

    def test_ask_applies_the_relatedness_rule(self):
        r = ask("What is the capital of France?", THREE, FakeBackend("Paris is the capital."), cfg(),
                now=NOW)
        self.assertEqual(r, AskResult(REFUSAL, "refused"))
        r = ask("What was the total price in the supplier quote?", THREE,
                FakeBackend("I can only answer questions about your recent windows."), cfg(), now=NOW)
        self.assertEqual(r, AskResult(DONT_KNOW, "unknown"))

    def test_build_prompt_masks_question(self):
        self.assertIn("[EMAIL]", build_prompt("mail bob@x.org?", ["1. A, just now"]))


if __name__ == "__main__":
    unittest.main()
