"""Clean-up of model-written recap lines (no real model: a fake backend)."""
import unittest

from app.config import Config
from app.summarizer import (cap_text, default_next, fallback_bullets, join_file_names,
                            restore_extension, rule_sentence, sanitize_bullets, summarize)
from app.titles import Entry

ENTRIES = [Entry(1, "Supplier Quotes.txt", "Notepad"),
           Entry(2, "Contract Draft v2.txt", "Notepad")]


class _Backend:
    name = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def generate_text(self, prompt):
        self.prompts.append(prompt)
        return self.answers.pop(0), {}


def cfg():
    c = Config()
    c.metrics_csv = ""
    return c


class JoinFileNameTests(unittest.TestCase):
    def test_the_seen_case(self):
        self.assertEqual(
            join_file_names("Next: Finish filling in the missing details in the Contract Draft v2. txt."),
            "Next: Finish filling in the missing details in the Contract Draft v2.txt.")

    def test_common_extensions(self):
        for ext in ("txt", "docx", "xlsx", "csv", "pdf", "py", "md", "pptx", "TXT", "Docx"):
            self.assertEqual(join_file_names(f"open report. {ext} now"), f"open report.{ext} now")

    def test_normal_sentences_are_left_alone(self):
        for text in ("You were done. Then you left.",
                     "Finish it. Text the team.",           # 'Text' is not 'txt'
                     "Check v2. Pdfs come later.",          # 'Pdfs' is a word, not '.pdf'
                     "Contract Draft v2.txt is open.",
                     "Done.  txt"):                          # two spaces: not one split name
            self.assertEqual(join_file_names(text), text)

    def test_model_line_is_fixed_in_the_recap(self):
        answer = ("Work: You were drafting a contract.\n"
                  "Next: Finish filling in the missing details in the Contract Draft v2. txt.")
        result = summarize(ENTRIES, cfg(), _Backend(answer))
        self.assertEqual(result["source"], "model")
        self.assertEqual(result["bullets"][2],
                         "Next: Finish filling in the missing details in the Contract Draft v2.txt.")

    def test_dropped_extension_at_the_end_is_restored(self):
        self.assertEqual(
            restore_extension("Finish the missing details in the Contract Draft v2.", ENTRIES),
            "Finish the missing details in the Contract Draft v2.txt.")
        for text in ("Contract Draft v2 is open.",              # not at the end
                     "Finish the Contract Draft v2.txt.",       # already whole
                     "Finish the Draft v2.",                    # not the whole name
                     "Finish the MyContract Draft v2."):        # part of a longer word
            self.assertEqual(restore_extension(text, ENTRIES), text)
        for prose in ("Open the contract draft v2.", "Finish reading the supplier quotes."):
            self.assertEqual(restore_extension(prose, ENTRIES), prose)   # other case: prose
        self.assertEqual(restore_extension("Finish reading Supplier Quotes.", ENTRIES),
                         "Finish reading Supplier Quotes.txt.")

    def test_seen_live_answer_gets_the_full_name(self):
        answer = ("Work: You were drafting a contract.\n"
                  "Next: Finish filling in the missing details in the Contract Draft v2.")
        result = summarize(ENTRIES, cfg(), _Backend(answer))
        self.assertEqual(result["bullets"][2],
                         "Next: Finish filling in the missing details in the Contract Draft v2.txt.")

    def test_rule_lines_are_not_changed(self):
        c = cfg()
        odd = [Entry(1, "Notes v2. txt", "Notepad")]           # a real title with that spacing
        self.assertEqual(fallback_bullets(odd, c)[0], "You stopped in Notes v2. txt (Notepad)")
        self.assertEqual(default_next(odd, c), "Next: continue in Notes v2. txt")
        result = summarize(odd, c, _Backend("Work: x\nNext: y"))
        self.assertEqual(result["bullets"][0], "You stopped in Notes v2. txt (Notepad)")


class CapTextTests(unittest.TestCase):
    LINE = "Next: Finish filling in the missing details in the Contract Draft v2.txt before lunch"

    def test_short_lines_are_untouched(self):
        self.assertEqual(cap_text("  a   short line ", 92), "a short line")

    def test_never_cuts_inside_a_word(self):
        for limit in range(20, len(self.LINE)):
            out = cap_text(self.LINE, limit)
            self.assertLessEqual(len(out), limit, limit)
            self.assertTrue(out.endswith("..."), out)
            head = out[:-3]
            words = self.LINE.split(" ")
            self.assertTrue(self.LINE.startswith(head + " ") or head in words, (limit, out))

    def test_never_cuts_a_file_name(self):
        for limit in range(60, len(self.LINE)):
            out = cap_text(self.LINE, limit)
            self.assertFalse(out[:-3].endswith(("v2", "v2.", "v2.t", "v2.tx")), out)

    def test_keeps_a_multi_word_title_whole(self):
        keep = ("Contract Draft v2.txt",)
        end = self.LINE.index("v2.txt") + len("v2.txt")              # 72
        for limit in range(55, end + 3):
            out = cap_text(self.LINE, limit, keep)
            self.assertNotIn("Contract", out, (limit, out))          # all or nothing
        self.assertIn("Contract Draft v2.txt...", cap_text(self.LINE, end + 3, keep))

    def test_ends_cleanly(self):
        out = cap_text("You were comparing the prices in the, Supplier Quotes", 40)
        self.assertEqual(out, "You were comparing the prices...")
        self.assertFalse(cap_text("Fix it now: then go home and rest", 16).endswith(":..."))

    def test_one_long_word_is_still_capped(self):
        out = cap_text("x" * 200, 92)
        self.assertEqual((len(out), out[-3:]), (92, "..."))

    def test_sanitize_protects_window_titles(self):
        c = cfg()
        c.max_bullet_chars = 60
        raw = "- You compared numbers before you went to Contract Draft v2.txt again today"
        out = sanitize_bullets(raw, c, ENTRIES)[0]
        self.assertLessEqual(len(out), 60)
        self.assertNotIn("Contract", out)
        self.assertTrue(out.endswith("..."))

    def test_rule_sentence_is_unchanged(self):
        e = Entry(1, "A Very Long Window Title About Supplier Quotes For The Third Quarter.txt", "")
        self.assertEqual(rule_sentence("You stopped in ", e, 40),
                         "You stopped in A Very Long Window Title")


if __name__ == "__main__":
    unittest.main()
