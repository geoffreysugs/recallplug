"""Editable block list: parsing, matching, files, overrides, engine reload."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import blocklist
from app import main as app_main
from app.config import Config
from app.engine import RecallEngine
from app.titles import is_blocked

SAMPLE_TITLES = [
    "My Bank - Online Banking - Chrome", "Reset PASSWORD - Edge", "New Incognito Tab - Chrome",
    "InPrivate - Microsoft Edge", "1Password", "Bitwarden", "KeePassXC", "LastPass Vault",
    "Login - Portal", "Sign in to your account", "Credit Card statement", "網銀 首頁",
    "修改密碼", "無痕視窗", "Q3 Forecast.xlsx - Excel", "Getting Started Guide - Word",
    "Contract Draft v2.txt - Notepad", "Signing in progress", "bankruptcy law - Chrome",
]


class ParseAndMatchTests(unittest.TestCase):
    def test_parse_skips_comments_blank_lines_and_duplicates(self):
        text = "# header\n\n  Bank  \n#password\nSign In\nbank\n\t\n"
        self.assertEqual(blocklist.parse(text), ("bank", "sign in"))

    def test_matching_is_case_insensitive_substring(self):
        self.assertTrue(blocklist.matches("My BANK account", ("bank",)))
        self.assertTrue(blocklist.matches("Please Sign In now", ("sign in",)))
        self.assertFalse(blocklist.matches("Q3 Forecast", ("bank",)))

    def test_shipped_file_equals_the_built_in_list(self):
        result = blocklist.load(blocklist.DEFAULT_PATH)
        self.assertEqual(result.source, "file")
        self.assertEqual(result.keywords, blocklist.default_keywords())
        self.assertEqual(result.keywords, tuple(Config.blacklist_keywords))

    def test_default_file_gives_identical_decisions(self):
        cfg = Config()
        keywords = blocklist.load(blocklist.DEFAULT_PATH).keywords
        for title in SAMPLE_TITLES:
            self.assertEqual(is_blocked(title, cfg, keywords), is_blocked(title, cfg), title)

    def test_render_round_trips(self):
        text = blocklist.render(("a", "b c"))
        self.assertTrue(text.startswith("# RecallPlug block list"))
        self.assertEqual(blocklist.parse(text), ("a", "b c"))


class FileTests(unittest.TestCase):
    def test_missing_file_is_created_from_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "blocklist.txt"
            result = blocklist.load(path)
            self.assertEqual((result.source, result.warning), ("created", None))
            self.assertEqual(result.keywords, blocklist.default_keywords())
            self.assertEqual(blocklist.load(path).keywords, blocklist.default_keywords())

    def test_unreadable_file_falls_back_with_one_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blocklist.txt"
            path.write_bytes(b"\xff\xfe\xfa bad utf-8 \x81")
            result = blocklist.load(path)
            self.assertEqual(result.source, "defaults")
            self.assertEqual(result.keywords, blocklist.default_keywords())
            self.assertIn("could not read", result.warning)

    def test_directory_instead_of_file_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = blocklist.load(Path(tmp))          # exists, but is a folder
            self.assertEqual(result.source, "defaults")
            self.assertIsNotNone(result.warning)

    def test_bom_from_notepad_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.txt"
            path.write_bytes("﻿secret project\n".encode("utf-8"))
            self.assertEqual(blocklist.load(path).keywords, ("secret project",))


class OverrideTests(unittest.TestCase):
    def test_cli_beats_env_beats_default(self):
        env = {blocklist.ENV_VAR: "from_env.txt"}
        self.assertEqual(blocklist.resolve_path("cli.txt", env), Path("cli.txt"))
        self.assertEqual(blocklist.resolve_path(None, env), Path("from_env.txt"))
        self.assertEqual(blocklist.resolve_path(None, {}), blocklist.DEFAULT_PATH)

    def test_main_prints_count_and_uses_the_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mine.txt"
            path.write_text("# mine\nalpha\nbeta\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                loader = app_main.load_blocklist(None, {blocklist.ENV_VAR: str(path)})
            self.assertIn("block list: 2 entries loaded from", out.getvalue())
            self.assertEqual(loader(), ("alpha", "beta"))

    def test_main_warns_once_for_an_unreadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.txt"
            path.write_bytes(b"\x81\x81")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                loader = app_main.load_blocklist(str(path), {})
                RecallEngine(Config(), get_title=str, get_idle=float, launch_hud=print,
                             blocklist_loader=loader)
            self.assertEqual(out.getvalue().count("warning:"), 1)
            self.assertIn("(built-in)", out.getvalue())

    def test_blocklist_flag_is_parsed(self):
        args = app_main.build_parser().parse_args(["--blocklist", "x.txt", "--demo"])
        self.assertEqual(args.blocklist, "x.txt")


class EngineReloadTests(unittest.TestCase):
    def test_reload_applies_edits_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.txt"
            path.write_text("bank\n", encoding="utf-8")
            title = ["Secret Project Plan.docx - Word"]
            cfg = Config()
            cfg.metrics_csv = ""
            engine = RecallEngine(cfg, get_title=lambda: title[0], get_idle=lambda: 0.0,
                                  launch_hud=lambda *a: None,
                                  blocklist_loader=blocklist.make_loader(path))
            self.assertEqual(engine.snapshot().blocklist_size, 1)
            self.assertTrue(engine.observe())
            path.write_text("bank\nSecret Project\n", encoding="utf-8")
            self.assertEqual(engine.reload_blocklist(), 2)
            title[0] = "Secret Project Notes.txt - Notepad"
            self.assertFalse(engine.observe())
            self.assertEqual(len(engine.snapshot().recent), 1)

    def test_failing_loader_keeps_the_old_list(self):
        events = []
        calls = []

        def loader():
            calls.append(1)
            if len(calls) > 1:
                raise OSError("disk gone")
            return ("bank",)
        engine = RecallEngine(Config(), get_title=str, get_idle=float, launch_hud=print,
                              blocklist_loader=loader, on_event=events.append)
        self.assertEqual(engine.reload_blocklist(), 1)
        self.assertEqual(events[0].message, "could not reload the block list (OSError)")

    def test_default_engine_uses_built_in_list(self):
        engine = RecallEngine(Config(), get_title=str, get_idle=float, launch_hud=print)
        self.assertEqual(engine.snapshot().blocklist_size, len(Config.blacklist_keywords))


if __name__ == "__main__":
    unittest.main()
