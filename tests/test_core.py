"""Run with:  python -m unittest -v      (no extra packages needed)"""
import csv
import json
import re
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.config import Config
from app.main import format_away
from app.hud import MIN_HEIGHT, TASKBAR_MARGIN, hud_geometry
from app.summarizer import (EXAMPLE_TITLES, build_prompt, cap_text, check_next, check_work,
                            condense_prompt, default_next, fallback_bullets, first_bullet,
                            log_metrics, main_words, mentions, risky_words, rule_sentence,
                            rule_work, sanitize_bullets, short_title, split_answer, summarize,
                            tidy_titles, names_last_two, file_names, names_file)
from app.titles import ActivityLog, Entry, clean_title, is_blocked


class TitleTests(unittest.TestCase):
    def test_office_title(self):
        self.assertEqual(clean_title("Q3 Forecast.xlsx - Excel"), ("Q3 Forecast.xlsx", "Excel"))

    def test_browser_title(self):
        self.assertEqual(clean_title("Inbox (3) - Google Chrome"), ("Inbox (3)", "Chrome"))

    def test_edge_title_with_profile_and_zero_width_space(self):
        raw = "Supplier Quotes - Personal - Microsoft\u200b Edge"
        self.assertEqual(clean_title(raw), ("Supplier Quotes", "Edge"))

    def test_vscode_keeps_file_and_project(self):
        self.assertEqual(clean_title("main.py - recallplug - Visual Studio Code"),
                         ("main.py - recallplug", "VS Code"))

    def test_unknown_app_keeps_full_title(self):
        self.assertEqual(clean_title("Some Tool"), ("Some Tool", ""))

    def test_chinese_notepad_title(self):
        self.assertEqual(clean_title("Q3 Supplier.txt - 記事本"), ("Q3 Supplier.txt", "Notepad"))
        self.assertEqual(clean_title("Contract Draft v2.txt - 記事本"),
                         ("Contract Draft v2.txt", "Notepad"))

    def test_chinese_file_explorer_title(self):
        self.assertEqual(clean_title("Downloads - 檔案總管"), ("Downloads", "File Explorer"))

    def test_blocked_exact_system_windows(self):
        cfg = Config()
        for title in ["工作切換", "Task View", "  工作檢視 ", "TASK SWITCHING"]:
            self.assertTrue(is_blocked(title, cfg), title)

    def test_project_pages_are_logged_but_our_hud_is_not(self):
        cfg = Config()
        self.assertNotIn("recallplug", cfg.ignore_titles)
        for title in ["geoffreysugs/recallplug: Hardware-offloaded workspace memory - Google Chrome",
                      "summarizer.py - recallplug - Visual Studio Code"]:
            self.assertFalse(is_blocked(title, cfg), title)
        for title in ["RecallPlug HUD", "recallplug hud", "  RecallPlug HUD "]:
            self.assertTrue(is_blocked(title, cfg), title)

    def test_new_tab_is_ignored_with_or_without_app_suffix(self):
        cfg = Config()
        for title in ["New Tab", "New Tab - Google Chrome", "new tab - Microsoft​ Edge",
                      "新分頁", "新分頁 - Google Chrome"]:
            self.assertTrue(is_blocked(title, cfg), title)
        for title in ["New Tab Page design ideas - Google Chrome", "新分頁設定 - Google Chrome",
                      "How to open a new tab - Google Chrome"]:
            self.assertFalse(is_blocked(title, cfg), title)

    def test_exact_list_does_not_block_normal_pages(self):
        cfg = Config()
        self.assertFalse(is_blocked("工作切換教學 - Google Chrome", cfg))
        self.assertFalse(is_blocked("Task View settings guide - Google Chrome", cfg))
        self.assertFalse(is_blocked("工作檢視.txt - 記事本", cfg))

    def test_blocked(self):
        cfg = Config()
        self.assertTrue(is_blocked("", cfg))
        self.assertTrue(is_blocked("My Bank - Online Banking - Chrome", cfg))
        self.assertTrue(is_blocked("RecallPlug HUD", cfg))
        self.assertTrue(is_blocked("1Password", cfg))
        self.assertTrue(is_blocked("New Incognito Tab - Chrome", cfg))
        self.assertFalse(is_blocked("Q3 Forecast.xlsx - Excel", cfg))
        self.assertFalse(is_blocked("Getting Started Guide - Word", cfg))

    def test_log_dedupes_consecutive(self):
        log = ActivityLog(5)
        self.assertTrue(log.add("A", ""))
        self.assertFalse(log.add("A", ""))
        self.assertTrue(log.add("B", ""))
        self.assertTrue(log.add("A", ""))
        self.assertEqual([e.title for e in log.recent(10)], ["A", "B", "A"])

    def test_log_is_limited(self):
        log = ActivityLog(3)
        for t in "ABCDE":
            log.add(t, "")
        self.assertEqual([e.title for e in log.recent(10)], ["C", "D", "E"])


class SummaryTextTests(unittest.TestCase):
    cfg = Config()

    def test_sanitize_strips_markers_and_caps(self):
        raw = "1. **Open** the budget\n- second point\n\u2022 third point\n- fourth point"
        out = sanitize_bullets(raw, self.cfg)
        self.assertEqual(out, ["Open the budget", "second point", "third point"])

    def test_sanitize_drops_json(self):
        raw = '{"image_summary": "x"}\n- real bullet'
        self.assertEqual(sanitize_bullets(raw, self.cfg), ["real bullet"])

    def test_cap_text_truncates(self):
        out = cap_text("x" * 200, 92)
        self.assertEqual(len(out), 92)
        self.assertTrue(out.endswith("..."))

    def test_empty_model_output(self):
        self.assertEqual(sanitize_bullets("  \n \n", self.cfg), [])

    def test_fallback_has_at_most_three(self):
        entries = [Entry(time.time(), t, "") for t in ["A", "B", "C", "D"]]
        out = fallback_bullets(entries, self.cfg)
        self.assertLessEqual(len(out), 3)
        self.assertIn("D", out[0])

    def test_fallback_empty(self):
        self.assertEqual(len(fallback_bullets([], self.cfg)), 1)

    def test_prompt_lists_oldest_first(self):
        entries = [Entry(1000, "First Doc", "Word"), Entry(2000, "Second Sheet", "Excel")]
        p = build_prompt(entries)
        listing = p[p.index("All recent windows", p.index("Now the real data")):]
        self.assertLess(listing.index("First Doc"), listing.index("Second Sheet"))
        self.assertIn("(Excel)", p)

    def test_prompt_anchors_last_two_windows(self):
        entries = [Entry(1000, "First Doc", "Word"), Entry(2000, "Second Sheet", "Excel"),
                   Entry(3000, "Plain Note", "")]
        real = build_prompt(entries).split("Now the real data:\n")[1]
        self.assertTrue(real.startswith(
            "Last window: Plain Note\nWindow before that: Second Sheet (Excel)\n"
            "All recent windows, oldest first:\n1. First Doc (Word)\n"))

    def test_prompt_single_window_has_no_before_anchor(self):
        real = build_prompt([Entry(1000, "First Doc", "Word")]).split("Now the real data:\n")[1]
        self.assertIn("Last window: First Doc (Word)\n", real)
        self.assertNotIn("Window before that", real)

    def test_prompt_asks_for_work_and_next_lines(self):
        p = build_prompt([Entry(1000, "First Doc", "Word")])
        self.assertIn("Write exactly 2 lines", p)
        self.assertIn("starts with 'Work: '", p)
        self.assertIn("judging from ALL recent windows", p)
        self.assertIn("do not copy any window title", p)
        self.assertIn("starts with 'Next: '", p)
        self.assertIn("continues the work shown (for example review, compare, update, finish, reply)", p)
        self.assertIn("must name the last window or the window before that", p)
        self.assertIn("Do not guess irreversible actions (deleting, uninstalling, sending, paying, "
                      "submitting) unless the window titles clearly show it", p)
        self.assertIn("Example answer:\nWork: ", p)

    def test_condense_prompt(self):
        p = condense_prompt("Next: a long sentence")
        self.assertTrue(p.startswith("Rewrite this sentence in under 80 characters"))
        self.assertIn("Sentence: Next: a long sentence\n", p)

    def test_num_predict_is_80(self):
        self.assertEqual(Config().num_predict, 80)

    def test_sanitize_drops_echoed_anchor_lines(self):
        raw = ("- Last window: Q3 Supplier.txt (Notepad)\n- window before that: Contract Draft v2.txt\n"
               "- You stopped in Q3 Supplier.txt")
        self.assertEqual(sanitize_bullets(raw, self.cfg), ["You stopped in Q3 Supplier.txt"])

    def test_main_words(self):
        self.assertEqual(main_words("Contract Draft v2.txt"), ["contract", "draft", "v2"])
        self.assertEqual(main_words("Example_Budget_Draft.xlsx"), ["example", "budget", "draft"])
        self.assertEqual(main_words("Claude Code for VS Code - Visual Studio Marketplace"),
                         ["claude", "code", "vs", "visual", "studio", "marketplace"])

    def test_mentions_needs_half_of_main_words(self):
        title = "Claude Code for VS Code - Visual Studio Marketplace"
        self.assertTrue(mentions("You stopped on the Claude Code VS page", title))   # 3 of 6
        self.assertFalse(mentions("You stopped on the Marketplace", title))          # 1 of 6
        self.assertTrue(mentions("anything", "a.txt"))                               # no main words

    def test_rule_sentences_for_bullets_1_and_2(self):
        entries = [Entry(1, "Q3 Supplier.txt", "Notepad"),
                   Entry(2, "Contract Draft v2.txt", "Notepad"),
                   Entry(3, "Claude Code for VS Code - Visual Studio Marketplace", "Chrome")]
        self.assertEqual(first_bullet(entries, self.cfg), "You stopped in Claude Code for VS Code (Chrome)")
        self.assertEqual(rule_work(entries, self.cfg), "Before that: Contract Draft v2.txt")
        self.assertIsNone(rule_work(entries[:1], self.cfg))

    def test_default_next_uses_short_title_without_app(self):
        entries = [Entry(1, "Extensions for Visual Studio family of products | Visual Studio Marketplace",
                         "Chrome")]
        self.assertEqual(default_next(entries, self.cfg),
                         "Next: continue in Extensions for Visual Studio family of products")

    def test_fallback_is_fully_rule_based(self):
        entries = [Entry(1, "Q3 Supplier.txt", "Notepad"), Entry(2, "Contract Draft v2.txt", "Notepad")]
        self.assertEqual(fallback_bullets(entries, self.cfg),
                         ["You stopped in Contract Draft v2.txt (Notepad)",
                          "Before that: Q3 Supplier.txt",
                          "Next: continue in Contract Draft v2.txt"])
        self.assertEqual(fallback_bullets(entries[:1], self.cfg),
                         ["You stopped in Q3 Supplier.txt (Notepad)", "Next: continue in Q3 Supplier.txt"])

    def test_short_title(self):
        self.assertEqual(short_title("Extensions for Visual Studio family of products | "
                                     "Visual Studio Marketplace"),
                         "Extensions for Visual Studio family of products")
        self.assertEqual(short_title("Claude Code for VS Code - Visual Studio Marketplace"),
                         "Claude Code for VS Code")
        self.assertEqual(short_title("Q3 Supplier.txt"), "Q3 Supplier.txt")

    _GH = "recallplug/tests/test_bench.py at main · geoffreysugs/recallplug"

    def test_short_title_github_pages(self):
        self.assertEqual(short_title(self._GH), "recallplug/tests/test_bench.py")
        self.assertEqual(short_title("recallplug/tests at main · geoffreysugs/recallplug"),
                         "recallplug/tests")
        self.assertEqual(short_title("Model infers task · geoffreysugs/recallplug@8429ab8"),
                         "Model infers task")
        self.assertEqual(short_title("Notes.txt at home"), "Notes.txt")
        # ' at <branch>' stays when the part before it is neither a path nor a file
        self.assertEqual(short_title("Meeting at noon"), "Meeting at noon")
        self.assertEqual(short_title("Lunch at Mia · Calendar"), "Lunch at Mia")

    def test_file_names(self):
        self.assertEqual(file_names(self._GH), {"test_bench.py"})
        self.assertEqual(file_names("recallplug/tests at main · geoffreysugs/recallplug"), {"tests"})
        self.assertEqual(file_names("Q3 Supplier.txt"), {"supplier.txt"})
        self.assertEqual(file_names("Weekly planning meeting"), set())

    def test_bullet_3_passes_with_the_file_name(self):
        entries = [Entry(1, "recallplug/.gitignore at main · geoffreysugs/recallplug", "Chrome"),
                   Entry(2, self._GH, "Chrome")]
        for n in (2, 6):
            cfg = Config()
            cfg.next_ground_windows = n
            self.assertIsNone(check_next("Next: Update the test_bench.py file with new functionality.",
                                         entries, cfg), n)
            self.assertIsNone(check_next("Next: review TEST_BENCH.PY", entries, cfg), n)
            why = check_next("Next: Finish the quarterly budget review.", entries, cfg)
            self.assertTrue(why.startswith(f"it names none of the last {n} windows"), n)
        self.assertTrue(names_last_two("Next: Update the test_bench.py file", entries))

    def test_file_name_must_be_a_whole_token(self):
        self.assertFalse(names_file("Next: update my_test_bench.pyc", self._GH))
        self.assertFalse(names_file("Next: update test_bench.python", self._GH))
        self.assertTrue(names_file("Next: update test_bench.py.", self._GH))

    def test_risky_words_still_reject_a_file_name_match(self):
        entries = [Entry(1, self._GH, "Chrome")]
        self.assertEqual(check_next("Next: delete test_bench.py", entries, self.cfg),
                         "it suggests a risky action (delete)")

    def test_bullet_2_file_name_counts_but_full_title_still_fails(self):
        entries = [Entry(1, "docs/a.md at main · u/r", "Chrome")]
        # 'a.md' shares no main word with the title (docs, main), but it is the file name
        self.assertIsNone(check_work("You were reading a.md", entries))
        self.assertEqual(check_work("You were on docs/a.md at main · u/r today", entries),
                         "it copies a full window title")
        self.assertEqual(check_work("You were busy.", entries),
                         "it shares no main word with any recent window")

    def test_github_preview_answer_is_accepted(self):
        """The real --preview answer whose bullet 3 used to be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            result, cfg, _ = _summarize_with(_GitHubOllama, _GITHUB, tmp)
            self.assertEqual(result["source"], "model")
            self.assertEqual(result["bullets"],
                             ["You stopped in recallplug/tests/test_bench.py (Chrome)",
                              "You were testing and debugging the recallplug application.",
                              "Next: Update the test_bench.py file with new functionality."])
            self.assertEqual(_read_rows(cfg.metrics_csv)[0]["next_names_last_two"], "true")

    def test_rule_sentence_uses_short_title_and_is_never_cut(self):
        e = Entry(1, "Extensions for Visual Studio family of products | Visual Studio Marketplace",
                  "Chrome")
        self.assertEqual(rule_sentence("Before that you were in ", e, 92),
                         "Before that you were in Extensions for Visual Studio family of products (Chrome)")
        long = Entry(1, "A very long window title without any separator " * 3, "Word")
        out = rule_sentence("You stopped in ", long, 92)
        self.assertLessEqual(len(out), 92)
        self.assertTrue(out.endswith(" (Word)"))
        self.assertNotIn("...", out)

    _WINDOWS = [Entry(1, "Meeting Notes.txt", "Notepad"),
                Entry(2, "Q3 Supplier.txt", "Notepad"),
                Entry(3, "Extensions for Visual Studio family of products | Visual Studio Marketplace",
                      "Chrome")]

    def test_split_answer_reads_labels_in_any_order(self):
        raw = "Answer: Next: review Q3 Supplier.txt\n- Work: You were checking supplier data."
        self.assertEqual(split_answer(raw, self.cfg, self._WINDOWS),
                         ("You were checking supplier data.", "review Q3 Supplier.txt"))

    def test_split_answer_without_labels(self):
        raw = "You were checking supplier data.\nreview Q3 Supplier.txt"
        self.assertEqual(split_answer(raw, self.cfg, self._WINDOWS),
                         ("You were checking supplier data.", "review Q3 Supplier.txt"))
        self.assertEqual(split_answer("", self.cfg, self._WINDOWS), (None, None))

    def test_split_answer_keeps_long_lines_whole(self):
        body = "You were " + "very " * 30 + "busy with supplier work."
        work, _ = split_answer("Work: " + body, self.cfg, self._WINDOWS)
        self.assertEqual(work, body)
        self.assertNotIn("...", work)

    def test_check_work(self):
        self.assertIsNone(check_work("You were comparing supplier quotes.", self._WINDOWS))
        self.assertEqual(check_work("You were editing Q3 Supplier.txt today", self._WINDOWS),
                         "it copies a full window title")
        self.assertEqual(check_work("You were editing meeting notes.txt", self._WINDOWS),
                         "it copies a full window title")       # case-insensitive
        self.assertEqual(check_work("You were busy.", self._WINDOWS),
                         "it shares no main word with any recent window")

    _SIX = [Entry(i, t, "Notepad") for i, t in enumerate(
        ["Old Budget.txt", "Meeting Notes.txt", "Contract Draft v2.txt", "Forecast.txt",
         "Q3 Supplier.txt", "Team Roster.txt", "Supplier Reviews and Ratings | Trade Forum"])]

    def test_next_ground_windows_default_is_6(self):
        self.assertEqual(Config().next_ground_windows, 6)

    def test_check_next_with_6_windows_accepts_any_recent_one(self):
        cfg = Config()                                           # next_ground_windows = 6
        for text in ["Next: finish Contract Draft v2.txt", "Next: update Meeting Notes.txt",
                     "Next: review Supplier Reviews and Ratings"]:
            self.assertIsNone(check_next(text, self._SIX, cfg), text)
        why = check_next("Next: close Old Budget.txt", self._SIX, cfg)   # 7th from the end
        self.assertTrue(why.startswith("it names none of the last 6 windows"))

    def test_check_next_with_2_windows_is_the_old_strict_rule(self):
        cfg = Config()
        cfg.next_ground_windows = 2
        self.assertIsNone(check_next("Next: review Supplier Reviews and Ratings", self._SIX, cfg))
        self.assertIsNone(check_next("Next: update Team Roster.txt", self._SIX, cfg))
        why = check_next("Next: finish Contract Draft v2.txt", self._SIX, cfg)
        self.assertTrue(why.startswith("it names none of the last 2 windows"))

    def test_risky_check_is_unchanged_by_ground_windows(self):
        for n in (2, 6):
            cfg = Config()
            cfg.next_ground_windows = n
            self.assertEqual(check_next("Next: delete Contract Draft v2.txt", self._SIX, cfg),
                             "it suggests a risky action (delete)")

    def test_names_last_two(self):
        self.assertTrue(names_last_two("Next: update Team Roster.txt", self._SIX))
        self.assertFalse(names_last_two("Next: finish Contract Draft v2.txt", self._SIX))

    def test_check_next_needs_one_of_the_recent_windows(self):
        self.assertIsNone(check_next("Next: review the Q3 Supplier figures", self._WINDOWS, self.cfg))
        self.assertIsNone(check_next("Next: compare Extensions for Visual Studio family of products",
                                     self._WINDOWS, self.cfg))
        self.assertIsNone(check_next("Next: finish Meeting Notes.txt", self._WINDOWS, self.cfg))
        why = check_next("Next: finish the budget", self._WINDOWS, self.cfg)
        self.assertTrue(why.startswith("it names none of the last 6 windows"))

    def test_check_next_rejects_risky_words(self):
        why = check_next("Next: uninstall Extensions for Visual Studio family of products",
                         self._WINDOWS, self.cfg)
        self.assertEqual(why, "it suggests a risky action (uninstall)")
        self.assertIn("send", check_next("Next: SEND the Q3 Supplier figures", self._WINDOWS, self.cfg))

    def test_risky_words_are_whole_words_and_configurable(self):
        cfg = Config()
        self.assertEqual(cfg.risky_next_words,
                         ("delete", "uninstall", "send", "pay", "purchase", "submit"))
        self.assertEqual(risky_words("Delete it, then Pay and submit.", cfg), ["delete", "pay", "submit"])
        self.assertEqual(risky_words("review the payroll, resend later, update payment", cfg), [])
        self.assertEqual(risky_words("close the tab, remove a row, cancel the draft", cfg), [])
        cfg.risky_next_words = ()
        self.assertEqual(risky_words("delete everything", cfg), [])
        self.assertIsNone(check_next("Next: delete Q3 Supplier.txt", self._WINDOWS, cfg))

    def test_tidy_titles_uses_short_titles_and_drops_trailing_app(self):
        self.assertEqual(
            tidy_titles("Next: open Extensions for Visual Studio family of products | "
                        "Visual Studio Marketplace (Chrome)", self._WINDOWS),
            "Next: open Extensions for Visual Studio family of products")
        self.assertEqual(tidy_titles("Next: update q3 supplier.txt (Notepad).", self._WINDOWS),
                         "Next: update Q3 Supplier.txt.")
        self.assertEqual(tidy_titles("Next: compare it with notes (draft)", self._WINDOWS),
                         "Next: compare it with notes (draft)")      # not an app name

    def test_sanitize_reports_dropped_lines(self):
        dropped = []
        sanitize_bullets("- Last window: X\n{json}\n- ok bullet here", self.cfg, (), dropped)
        self.assertEqual(dropped, ["echoed anchor line: Last window: X", "looks like JSON: {json}"])

    def test_prompt_has_no_clock_times(self):
        entries = [Entry(1000, "First Doc", "Word"), Entry(2000, "Second Sheet", "Excel"),
                   Entry(3000, "Plain Note", "")]
        p = build_prompt(entries)
        self.assertIn("1. First Doc (Word)\n", p)
        self.assertIn("2. Second Sheet (Excel)\n", p)
        self.assertIn("3. Plain Note\n", p)
        self.assertIsNone(re.search(r"\d{1,2}:\d{2}", p))

    def test_prompt_has_roles_and_fake_example(self):
        p = build_prompt([Entry(1000, "First Doc", "Word")])
        self.assertIn("Do not copy the window list", p)
        for title in EXAMPLE_TITLES:
            self.assertIn(title, p)
        # the example comes before the real windows
        self.assertLess(p.index(EXAMPLE_TITLES[0]), p.index("First Doc"))

    def test_sanitize_removes_clock_tags(self):
        raw = "- [00:45] Opened the budget sheet\n- [9:05:30] Read the contract\n- Meeting at 14:00 with Mia"
        self.assertEqual(sanitize_bullets(raw, self.cfg),
                         ["Opened the budget sheet", "Read the contract", "Meeting at 14:00 with Mia"])

    def test_sanitize_drops_copied_titles(self):
        entries = [Entry(1000, "Claude Code for VS Code - Visual Studio Marketplace", "Chrome"),
                   Entry(2000, "Plain Note", "")]
        raw = ("- [00:45] Claude Code for VS Code - Visual Studio Marketplace (Chrome)\n"
               "- plain note.\n"
               "- Resumed from Claude Code for VS Code.")
        self.assertEqual(sanitize_bullets(raw, self.cfg, entries),
                         ["Resumed from Claude Code for VS Code."])

    def test_sanitize_keeps_bullets_that_mention_a_title(self):
        entries = [Entry(1000, "Q3 Forecast.xlsx", "Excel")]
        raw = "- You were in Q3 Forecast.xlsx\n- Check the unit price column"
        self.assertEqual(sanitize_bullets(raw, self.cfg, entries),
                         ["You were in Q3 Forecast.xlsx", "Check the unit price column"])

    def test_sanitize_drops_example_copies(self):
        raw = ("- You stopped in Example_Budget_Draft.xlsx in Excel\n"
               "- Before that you were checking prices on example supplier portal\n"
               "- You stopped in Q3 Forecast.xlsx")
        self.assertEqual(sanitize_bullets(raw, self.cfg), ["You stopped in Q3 Forecast.xlsx"])

    def test_config_uses_ipv4_loopback(self):
        self.assertTrue(Config().ollama_url.startswith("http://127.0.0.1:11434/"))

    def test_hud_height_follows_text_and_grows_upward(self):
        bottoms = set()
        for wanted in (50, 200, 400):
            w, h, x, y = hud_geometry(wanted, 1920, 1080)
            self.assertEqual(h, max(MIN_HEIGHT, wanted))
            self.assertEqual(x, 1920 - w - 20)
            bottoms.add(y + h)
        self.assertEqual(bottoms, {1080 - TASKBAR_MARGIN})          # bottom edge stays fixed

    def test_hud_max_height_is_half_the_real_screen(self):
        self.assertEqual(hud_geometry(5000, 1920, 1080)[1], 540)
        self.assertEqual(hud_geometry(5000, 3840, 2160)[1], 1080)
        _, h, _, y = hud_geometry(5000, 1920, 1080)
        self.assertEqual(y + h, 1080 - TASKBAR_MARGIN)

    def test_format_away(self):
        self.assertEqual(format_away(10), "10 s")
        self.assertEqual(format_away(300), "5 min")


class _FakeOllama(BaseHTTPRequestHandler):
    response = ("Work: You were checking the Q3 unit prices.\n"
                "Next: check the unit price column in Q3 Forecast.xlsx")
    condensed = ""                       # answer to a "Rewrite this sentence" prompt

    def do_POST(self):
        prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["prompt"]
        self.server.prompts.append(prompt)
        payload = {
            "response": self.condensed if prompt.startswith("Rewrite") else self.response,
            "load_duration": 500_000_000, "prompt_eval_duration": 250_000_000,
            "eval_count": 40, "eval_duration": 5_000_000_000, "total_duration": 6_000_000_000,
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class _CopyingOllama(_FakeOllama):
    """A model that just repeats the window list with clock times."""
    response = "- [00:45] Q3 Forecast.xlsx (Excel)\n- [00:46] Contract_Draft.docx (Word)"


class _RiskyOllama(_FakeOllama):
    """Like a real qwen2.5:1.5b answer: a fine bullet 2, but bullet 3 says 'uninstall'."""
    response = ("Work: You were browsing Visual Studio extensions.\n"
                "Next: uninstall the Extensions for Visual Studio family of products | "
                "Visual Studio Marketplace (Chrome)")


class _OlderDocOllama(_FakeOllama):
    """Like web_heavy: bullet 3 names an older document, not one of the last two windows."""
    response = ("Work: You were reviewing the contract and the team roster.\n"
                "Next: finish Contract Draft v2.txt")


class _GitHubOllama(_FakeOllama):
    """The real qwen2.5:1.5b answer from a --preview over GitHub pages."""
    response = ("Work: You were testing and debugging the recallplug application.\n"
                "Next: Update the test_bench.py file with new functionality.")


_GITHUB = [Entry(1, "geoffreysugs/recallplug: Hardware-offloaded workspace memory for ASUS UGen300", "Chrome"),
           Entry(2, "recallplug/tests at main · geoffreysugs/recallplug", "Chrome"),
           Entry(3, "recallplug/tests/test_redact.py at main · geoffreysugs/recallplug", "Chrome"),
           Entry(4, "recallplug/.gitignore at main · geoffreysugs/recallplug", "Chrome"),
           Entry(5, "recallplug/tests/test_bench.py at main · geoffreysugs/recallplug", "Chrome")]


_LONG_NEXT = ("Next: compare the unit price column in Q3 Forecast.xlsx with every supplier quote "
              "you received during the last three months")


class _LongOllama(_FakeOllama):
    """Bullet 3 is too long; the condense retry fixes it."""
    response = "Work: You were checking the Q3 unit prices.\n" + _LONG_NEXT
    condensed = "Next: compare Q3 Forecast.xlsx unit prices with recent supplier quotes"


class _StillLongOllama(_LongOllama):
    """The condense retry does not help."""
    condensed = _LONG_NEXT


_WEB = [Entry(1, "Q3 Supplier.txt", "Notepad"),
        Entry(2, "Contract Draft v2.txt", "Notepad"),
        Entry(3, "Extensions for Visual Studio family of products | Visual Studio Marketplace", "Chrome")]


def _summarize_with(handler, entries, tmp, cfg=None):
    """Returns (result, cfg, prompts the fake server received)."""
    server = HTTPServer(("127.0.0.1", 0), handler)
    server.prompts = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = cfg or Config()
    cfg.ollama_url = f"http://127.0.0.1:{server.server_port}/api/generate"
    cfg.metrics_csv = str(Path(tmp) / "m.csv")
    try:
        return summarize(entries, cfg), cfg, server.prompts
    finally:
        server.shutdown()
        server.server_close()


def _read_rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


class OllamaIntegrationTests(unittest.TestCase):
    def test_model_path_and_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, cfg, prompts = _summarize_with(
                _FakeOllama, [Entry(time.time(), "Q3 Forecast.xlsx", "Excel")], tmp)
            self.assertEqual(result["source"], "model")
            self.assertEqual(result["bullets"], ["You stopped in Q3 Forecast.xlsx (Excel)",
                                                 "You were checking the Q3 unit prices.",
                                                 "Next: check the unit price column in Q3 Forecast.xlsx"])
            self.assertEqual(len(prompts), 1)                         # one call for bullets 2 and 3
            m = result["metrics"]
            self.assertEqual(m["tokens_per_s"], 8.0)
            self.assertEqual(m["ttft_approx_s"], 0.75)
            rows = _read_rows(cfg.metrics_csv)
            self.assertEqual(len(rows), 1)
            self.assertIn("MEASURED", rows[0]["label"])
            self.assertEqual((rows[0]["output_used"], rows[0]["bullets_replaced"],
                              rows[0]["condense_retries"]), ("model", "0", "0"))

    def test_copied_output_replaces_both_bullets(self):
        entries = [Entry(1000, "Q3 Forecast.xlsx", "Excel"),
                   Entry(2000, "Contract_Draft.docx", "Word")]
        with tempfile.TemporaryDirectory() as tmp:
            result, cfg, _ = _summarize_with(_CopyingOllama, entries, tmp)
            self.assertEqual(result["source"], "mixed")
            self.assertEqual(result["bullets"], fallback_bullets(entries, cfg))
            rows = _read_rows(cfg.metrics_csv)
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["output_used"], rows[0]["bullets_replaced"]), ("mixed", "2"))

    def test_risky_next_is_replaced_and_bullet_2_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, cfg, _ = _summarize_with(_RiskyOllama, _WEB, tmp)
            self.assertEqual(result["source"], "mixed")
            self.assertEqual(result["bullets"],
                             ["You stopped in Extensions for Visual Studio family of products (Chrome)",
                              "You were browsing Visual Studio extensions.",
                              "Next: continue in Extensions for Visual Studio family of products"])
            replaced = result["debug"]["replaced"]
            self.assertEqual(len(replaced), 1)
            self.assertTrue(replaced[0].startswith(
                "bullet 3 replaced: it suggests a risky action (uninstall): uninstall the Extensions"))
            self.assertEqual(_read_rows(cfg.metrics_csv)[0]["bullets_replaced"], "1")

    def test_long_sentence_is_condensed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, cfg, prompts = _summarize_with(_LongOllama, [Entry(1, "Q3 Forecast.xlsx", "Excel")], tmp)
            self.assertEqual(result["source"], "model")
            self.assertEqual(result["bullets"][2], _LongOllama.condensed)
            self.assertEqual(len(prompts), 2)
            self.assertTrue(prompts[1].startswith("Rewrite this sentence in under 80 characters"))
            self.assertIn(_LONG_NEXT, prompts[1])
            self.assertEqual(result["metrics"]["condense_retries"], 1)
            self.assertEqual(_read_rows(cfg.metrics_csv)[0]["condense_retries"], "1")

    def test_still_long_after_retry_becomes_rule_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, cfg, prompts = _summarize_with(_StillLongOllama,
                                                   [Entry(1, "Q3 Forecast.xlsx", "Excel")], tmp)
            self.assertEqual(len(prompts), 2)                       # only ONE retry
            self.assertEqual(result["source"], "mixed")
            self.assertEqual(result["bullets"][-1], "Next: continue in Q3 Forecast.xlsx")
            self.assertTrue(result["debug"]["replaced"][0].startswith(
                f"bullet 3 replaced: still {len(_LONG_NEXT)} characters after one retry"))
            self.assertEqual((result["metrics"]["bullets_replaced"],
                              result["metrics"]["condense_retries"]), (1, 1))

    def test_no_bullet_is_ever_cut_with_dots(self):
        for handler in (_FakeOllama, _CopyingOllama, _RiskyOllama, _LongOllama, _StillLongOllama):
            with tempfile.TemporaryDirectory() as tmp:
                result, _, _ = _summarize_with(handler, _WEB, tmp)
                for b in result["bullets"]:
                    self.assertNotIn("...", b, handler.__name__)
                    self.assertLessEqual(len(b), 92, handler.__name__)

    def test_metrics_column_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, cfg, _ = _summarize_with(_FakeOllama, [Entry(1, "Q3 Forecast.xlsx", "Excel")], tmp)
            self.assertEqual(list(_read_rows(cfg.metrics_csv)[0])[-4:],
                             ["output_used", "bullets_replaced", "condense_retries",
                              "next_names_last_two"])

    def test_next_names_last_two_column(self):
        # (handler, ground windows, expected output_used, expected next_names_last_two)
        old_doc = [Entry(1, "Contract Draft v2.txt", "Notepad"), Entry(2, "Team Roster.txt", "Notepad"),
                   Entry(3, "Trade Forum Home", "Chrome")]
        cases = [(_FakeOllama, [Entry(1, "Q3 Forecast.xlsx", "Excel")], 6, "model", "true"),
                 (_OlderDocOllama, old_doc, 6, "model", "false"),
                 (_OlderDocOllama, old_doc, 2, "mixed", ""),
                 (_RiskyOllama, _WEB, 6, "mixed", "")]
        for handler, entries, n, used, flag in cases:
            cfg = Config()
            cfg.next_ground_windows = n
            with tempfile.TemporaryDirectory() as tmp:
                result, cfg, _ = _summarize_with(handler, entries, tmp, cfg)
                row = _read_rows(cfg.metrics_csv)[0]
                self.assertEqual((row["output_used"], row["next_names_last_two"]), (used, flag),
                                 (handler.__name__, n))
                self.assertIn(row["next_names_last_two"], ("true", "false", ""))

    def test_no_model_is_rule_only_without_any_call(self):
        cfg = Config()
        cfg.use_model = False
        cfg.ollama_url = "http://127.0.0.1:9/api/generate"   # would fail if it were called
        with tempfile.TemporaryDirectory() as tmp:
            cfg.metrics_csv = str(Path(tmp) / "m.csv")
            result = summarize(_WEB, cfg)
            self.assertEqual(result["source"], "rule_only")
            self.assertIsNone(result["metrics"])
            self.assertEqual(result["bullets"],
                             ["You stopped in Extensions for Visual Studio family of products (Chrome)",
                              "Before that: Contract Draft v2.txt",
                              "Next: continue in Extensions for Visual Studio family of products"])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_debug_info_is_returned_but_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, cfg, _ = _summarize_with(_RiskyOllama, _WEB, tmp)
            d = result["debug"]
            self.assertEqual(d["backend"], "ollama")
            self.assertEqual(d["raw"], _RiskyOllama.response)
            self.assertTrue(d["replaced"][0].startswith("bullet 3 replaced"))
            saved = Path(cfg.metrics_csv).read_text(encoding="utf-8")
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)       # only the CSV
            for e in _WEB:
                self.assertNotIn(e.title, saved)
            for word in ("Supplier", "Contract", "Visual", "uninstall", "browsing"):
                self.assertNotIn(word, saved)

    def test_metrics_csv_empty_string_turns_logging_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
            server.prompts = []
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cfg = Config()
            cfg.ollama_url = f"http://127.0.0.1:{server.server_port}/api/generate"
            cfg.metrics_csv = ""
            try:
                result = summarize([Entry(1, "Q3 Forecast.xlsx", "Excel")], cfg)
            finally:
                server.shutdown()
                server.server_close()
            self.assertEqual(result["source"], "model")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_falls_back_when_ollama_down(self):
        cfg = Config()
        cfg.ollama_url = "http://127.0.0.1:9/api/generate"   # nothing listens here
        cfg.request_timeout_s = 2
        result = summarize([Entry(time.time(), "Doc", "")], cfg)
        self.assertEqual(result["source"], "fallback")
        self.assertTrue(result["bullets"])

    def test_metrics_header_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "sub" / "m.csv")
            log_metrics(path, {"a": 1, "b": 2})
            log_metrics(path, {"a": 3, "b": 4})
            lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(lines, ["a,b", "1,2", "3,4"])
            self.assertEqual(list(Path(tmp, "sub").iterdir()), [Path(path)])

    def test_old_header_file_is_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bench" / "metrics.csv"
            path.parent.mkdir()
            path.write_text("timestamp,model\n2026-10-25 00:45:58,qwen2.5:1.5b\n", encoding="utf-8")
            log_metrics(str(path), {"timestamp": "t1", "model": "m", "output_used": "model"})
            old = path.with_name("metrics_first_runs.csv")
            self.assertEqual(old.read_text(encoding="utf-8"),
                             "timestamp,model\n2026-10-25 00:45:58,qwen2.5:1.5b\n")
            self.assertEqual(path.read_text(encoding="utf-8").splitlines(),
                             ["timestamp,model,output_used", "t1,m,model"])

    def test_header_without_bullets_replaced_is_renamed(self):
        old_header = ("timestamp,model,load_s,ttft_approx_s,tokens,tokens_per_s,wall_s,"
                      "host_cpu_pct,host_ram_pct,label,output_used")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.csv"
            path.write_text(old_header + "\nrow\n", encoding="utf-8")
            path.with_name("metrics_first_runs.csv").write_text("earliest\n", encoding="utf-8")
            new = dict.fromkeys(old_header.split(","), "x")
            new["bullets_replaced"] = 0
            log_metrics(str(path), new)
            self.assertEqual(path.with_name("metrics_first_runs_2.csv").read_text(encoding="utf-8"),
                             old_header + "\nrow\n")
            self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0],
                             old_header + ",bullets_replaced")

    def test_header_without_condense_retries_is_renamed(self):
        old_header = ("timestamp,model,load_s,ttft_approx_s,tokens,tokens_per_s,wall_s,"
                      "host_cpu_pct,host_ram_pct,label,output_used,bullets_replaced")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.csv"
            path.write_text(old_header + "\nrow\n", encoding="utf-8")
            for name in ("metrics_first_runs.csv", "metrics_first_runs_2.csv"):
                path.with_name(name).write_text("earlier\n", encoding="utf-8")
            new = dict.fromkeys(old_header.split(","), "x")
            new["condense_retries"] = 0
            log_metrics(str(path), new)
            self.assertEqual(path.with_name("metrics_first_runs_3.csv").read_text(encoding="utf-8"),
                             old_header + "\nrow\n")
            self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0],
                             old_header + ",condense_retries")

    def test_header_without_next_names_last_two_is_renamed(self):
        old_header = ("timestamp,model,load_s,ttft_approx_s,tokens,tokens_per_s,wall_s,"
                      "host_cpu_pct,host_ram_pct,label,output_used,bullets_replaced,condense_retries")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.csv"
            path.write_text(old_header + "\nrow\n", encoding="utf-8")
            new = dict.fromkeys(old_header.split(","), "x")
            new["next_names_last_two"] = "true"
            log_metrics(str(path), new)
            self.assertEqual(path.with_name("metrics_first_runs.csv").read_text(encoding="utf-8"),
                             old_header + "\nrow\n")
            self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0],
                             old_header + ",next_names_last_two")

    def test_rename_never_overwrites_an_earlier_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.csv"
            path.with_name("metrics_first_runs.csv").write_text("first\n", encoding="utf-8")
            path.write_text("a\n1\n", encoding="utf-8")
            log_metrics(str(path), {"a": 2, "b": 3})
            self.assertEqual(path.with_name("metrics_first_runs.csv").read_text(encoding="utf-8"), "first\n")
            self.assertEqual(path.with_name("metrics_first_runs_2.csv").read_text(encoding="utf-8"), "a\n1\n")
            self.assertEqual(path.read_text(encoding="utf-8").splitlines(), ["a,b", "2,3"])


if __name__ == "__main__":
    unittest.main()
