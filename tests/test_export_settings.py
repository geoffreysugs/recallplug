"""Export file and settings: temp folders only."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from app import export, settings
from app.config import Config
from app.engine import Snapshot, State, WindowEntry
from app.gui import build_config, build_parser

NOW = time.mktime((2026, 11, 8, 14, 30, 5, 0, 0, -1))


def snap(recap=("You stopped in A.txt (Notepad)", "You were writing.", "Next: finish A.txt")):
    recent = (WindowEntry(NOW - 120, "Supplier Quotes.txt", "Notepad"),
              WindowEntry(NOW - 60, "Mail from [EMAIL]", "Outlook"),
              WindowEntry(NOW - 5, "Contract Draft v2.txt", ""))
    return Snapshot(State.RUNNING, recent, recap, 0.0, 14)


class ExportTests(unittest.TestCase):
    def test_file_name_and_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export.export_snapshot(snap(), Path(tmp), NOW)
            self.assertEqual(path.name, "recallplug_20261108_143005.txt")
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            self.assertEqual(lines[0], "Recall Plug export - 2026-11-08 14:30:05")
            self.assertEqual(lines[3], "2026-11-08 14:28:05  Supplier Quotes.txt (Notepad)")
            self.assertEqual(lines[5], "2026-11-08 14:30:00  Contract Draft v2.txt")
            self.assertIn("Mail from [EMAIL] (Outlook)", text)          # stays masked
            self.assertEqual(lines[-3:], ["- You stopped in A.txt (Notepad)",
                                          "- You were writing.", "- Next: finish A.txt"])
            self.assertIn(b"\r\n", path.read_bytes())                      # Notepad-friendly

    def test_same_second_gets_a_new_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = export.export_snapshot(snap(), Path(tmp), NOW)
            b = export.export_snapshot(snap(), Path(tmp), NOW)
            self.assertNotEqual(a, b)
            self.assertEqual(b.name, "recallplug_20261108_143005_2.txt")

    def test_empty_parts(self):
        text = export.export_text(Snapshot(State.RUNNING, (), None, 0.0, 0), NOW)
        self.assertIn("Recent windows (oldest first):\n(none)", text)
        self.assertTrue(text.endswith("Latest recap:\n(none)\n"))

    def test_creates_the_folder_and_default_is_exports(self):
        self.assertEqual(export.EXPORT_DIR.name, "exports")
        with tempfile.TemporaryDirectory() as tmp:
            path = export.export_snapshot(snap(None), Path(tmp) / "new" / "dir", NOW)
            self.assertTrue(path.exists())

    def test_exports_folder_is_git_ignored(self):
        ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("exports/", ignore.split())
        self.assertIn("config/settings.json", ignore.split())


class SettingsTests(unittest.TestCase):
    def test_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, warning = settings.load(Path(tmp) / "settings.json")
        self.assertEqual(s, settings.Settings(5, False, "qwen2.5:1.5b"))
        self.assertIsNone(warning)

    def test_save_creates_and_load_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg" / "settings.json"
            settings.save(settings.Settings(12, True, "llama3.2:1b"), path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"away_minutes": 12, "demo": True, "model": "llama3.2:1b"})
            self.assertEqual(settings.load(path), (settings.Settings(12, True, "llama3.2:1b"), None))

    def test_validation(self):
        ok, problems = settings.validate({"away_minutes": "7", "demo": False, "model": " m:1 "})
        self.assertEqual((ok, problems), (settings.Settings(7, False, "m:1"), []))
        for bad in ({"away_minutes": 0}, {"away_minutes": 61}, {"away_minutes": "x"},
                    {"away_minutes": 2.5}, {"away_minutes": True}, {"demo": "yes"},
                    {"model": ""}, {"model": "bad name!"}, {"model": 5}):
            s, problems = settings.validate(bad)
            self.assertEqual(len(problems), 1, bad)
            self.assertEqual(s, settings.Settings(), bad)              # bad value -> default
        with self.assertRaises(ValueError):
            settings.save(settings.Settings(0, False, "m"), Path("never_written.json"))
        self.assertFalse(Path("never_written.json").exists())

    def test_broken_file_gives_defaults_and_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            s, warning = settings.load(path)
            self.assertEqual(s, settings.Settings())
            self.assertIn("could not read", warning)
            path.write_text('{"away_minutes": 99, "model": "ok:1"}', encoding="utf-8")
            s, warning = settings.load(path)
            self.assertEqual(s, settings.Settings(5, False, "ok:1"))
            self.assertIn("1 to 60", warning)

    def test_override_order_defaults_file_flags(self):
        saved = settings.Settings(12, False, "file-model:1")
        cfg = build_config(build_parser().parse_args([]), None)
        self.assertEqual((cfg.away_s, cfg.model), (300, Config.model))          # defaults
        cfg = build_config(build_parser().parse_args([]), saved)
        self.assertEqual((cfg.away_s, cfg.model), (720, "file-model:1"))        # file
        cfg = build_config(build_parser().parse_args(["--demo", "--model", "cli:2"]), saved)
        self.assertEqual((cfg.away_s, cfg.model), (10, "cli:2"))                # flags win
        cfg = build_config(build_parser().parse_args([]), settings.Settings(12, True, "m"))
        self.assertEqual(cfg.away_s, 10)                                        # demo setting


if __name__ == "__main__":
    unittest.main()
