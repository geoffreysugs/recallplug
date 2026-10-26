import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from app.config import Config
from bench.run_bench import (FIELDS, SCENARIOS, build_summary, new_run_dir, percentile,
                             run_bench, save_run)


def _mock_cfg():
    cfg = Config()
    cfg.backend = "mock"
    return cfg


def _quiet_run(runs, cfg, scenarios=None):
    with contextlib.redirect_stdout(io.StringIO()):
        return run_bench(runs, cfg, scenarios)


class BenchTests(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        values = list(range(1, 21))            # 1..20
        self.assertEqual(percentile(values, 95), 19)
        self.assertEqual(percentile(values, 50), 10)
        self.assertEqual(percentile([7.0], 95), 7.0)

    def test_web_heavy_scenario_ends_with_two_chrome_pages(self):
        web = SCENARIOS["web_heavy"]
        self.assertEqual([app for _t, app in web[-2:]], ["Chrome", "Chrome"])
        self.assertTrue(all(t.endswith(".txt") and app == "Notepad" for t, app in web[:-2]))

    def test_runs_every_scenario_by_default(self):
        cfg = _mock_cfg()
        rows = _quiet_run(2, cfg)
        self.assertEqual([r["scenario"] for r in rows], ["office", "office", "web_heavy", "web_heavy"])
        self.assertTrue(all(r["output_used"] == "model" for r in rows))
        self.assertEqual(cfg.metrics_csv, "")     # the bench never touches metrics.csv

    def test_summary_reports_scenarios_separately(self):
        cfg = _mock_cfg()
        summary = build_summary(_quiet_run(2, cfg), cfg, "results/x")
        self.assertIn("## Scenario: office (MEASURED (host))", summary)
        self.assertIn("## Scenario: web_heavy (MEASURED (host))", summary)
        self.assertEqual(summary.count("| model | 2 |"), 2)
        self.assertEqual(summary.count("| mixed | 0 |"), 2)
        self.assertEqual(summary.count("| fallback | 0 |"), 2)
        self.assertEqual(summary.count("| bullets_replaced | Runs |\n|---|---|\n| 0 | 2 |\n| 1 | 0 |"), 2)
        for needle in ["| wall_s |", "| ttft_approx_s |", "| tokens_per_s |", "| host_cpu_pct |",
                       "| host_ram_pct |", "Median", "p95", "| OS |", "| Python |",
                       "`results/x/results.csv`"]:
            self.assertIn(needle, summary)

    def test_save_run_keeps_every_run_and_updates_latest_summary(self):
        cfg = _mock_cfg()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first = save_run(_quiet_run(1, cfg), cfg, base)
            second = save_run(_quiet_run(1, cfg, ["web_heavy"]), cfg, base)
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, base / "results")
            for run_dir in (first, second):
                self.assertTrue((run_dir / "results.csv").exists())
                self.assertTrue((run_dir / "summary.md").exists())
            self.assertEqual((base / "summary.md").read_text(encoding="utf-8"),
                             (second / "summary.md").read_text(encoding="utf-8"))

            with open(first / "results.csv", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, FIELDS)
                saved = list(reader)
            self.assertEqual(len(saved), 2)
            self.assertTrue(all(r["label"] == "MEASURED (host)" for r in saved))

            text = "".join(p.read_text(encoding="utf-8") for p in base.rglob("*") if p.is_file())
            for windows in SCENARIOS.values():
                for title, _app in windows:
                    self.assertNotIn(title, text)
            self.assertNotIn("pick up where you left off", text)   # raw answers are never saved

    def test_summary_has_condense_retries_table(self):
        cfg = _mock_cfg()
        summary = build_summary(_quiet_run(2, cfg, ["office"]), cfg)
        self.assertIn("| condense_retries | Runs |\n|---|---|\n| 0 | 2 |\n| 1 | 0 |\n| 2 | 0 |", summary)
        self.assertIn("| rule_only | 0 |", summary)

    def test_no_model_rows_are_rule_only(self):
        cfg = Config()
        cfg.use_model = False
        cfg.ollama_url = "http://127.0.0.1:9/api/generate"      # would fail if it were called
        rows = _quiet_run(2, cfg)
        self.assertTrue(all(r["output_used"] == "rule_only" for r in rows))
        self.assertTrue(all(r["backend"] == "none" and r["wall_s"] == "" for r in rows))
        summary = build_summary(rows, cfg)
        self.assertEqual(summary.count("| rule_only | 2 |"), 2)
        self.assertIn("| wall_s | n/a | n/a | 0 |", summary)
        self.assertIn("none (rule-only)", summary)

    def test_show_rejections_prints_reasons_to_terminal_only(self):
        cfg = Config()
        cfg.backend = "mock"
        cfg.risky_next_words = ("pick",)          # the mock's "Next: pick up ..." is now risky
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(out):
            rows = run_bench(1, cfg, ["office"], show_rejections=True)
            save_run(rows, cfg, Path(tmp))
            saved = "".join(p.read_text(encoding="utf-8") for p in Path(tmp).rglob("*") if p.is_file())
        self.assertIn("    - bullet 3 replaced: it suggests a risky action (pick)", out.getvalue())
        self.assertNotIn("risky", saved)
        self.assertEqual(rows[0]["output_used"], "mixed")

    def test_summary_shows_ground_windows_and_strict_flag(self):
        cfg = _mock_cfg()
        cfg.next_ground_windows = 2
        rows = _quiet_run(2, cfg, ["web_heavy"])
        self.assertTrue(all(r["next_names_last_two"] == "true" for r in rows))   # mock names the last window
        summary = build_summary(rows, cfg)
        self.assertIn("| Bullet 3 ground windows | 2 |", summary)
        self.assertIn("| next_names_last_two (accepted bullet 3) | Runs |\n|---|---|\n"
                      "| true | 2 |\n| false | 0 |\n| n/a (replaced) | 0 |", summary)

    def test_cpu_only_rows_and_summary_are_labelled(self):
        cfg = _mock_cfg()
        cfg.cpu_only = True
        rows = _quiet_run(1, cfg, ["office"])
        self.assertEqual((rows[0]["cpu_only"], rows[0]["label"]), ("true", "MEASURED (host, CPU only)"))
        summary = build_summary(rows, cfg)
        self.assertIn("**MEASURED (host, CPU only)**", summary)
        self.assertIn("with Ollama option num_gpu = 0", summary)
        self.assertIn("## Scenario: office (MEASURED (host, CPU only))", summary)
        self.assertIn("| cpu_only | true |", summary)

    def test_default_rows_are_not_cpu_only(self):
        cfg = _mock_cfg()
        rows = _quiet_run(1, cfg, ["office"])
        self.assertEqual((rows[0]["cpu_only"], rows[0]["label"]), ("false", "MEASURED (host)"))
        summary = build_summary(rows, cfg)
        self.assertIn("| cpu_only | false |", summary)
        self.assertNotIn("num_gpu", summary)

    def test_fields_have_peak_cpu_and_cpu_only(self):
        self.assertEqual(FIELDS.index("host_cpu_peak_pct"), FIELDS.index("host_cpu_pct") + 1)
        self.assertEqual(FIELDS[-2:], ["cpu_only", "label"])
        summary = build_summary(_quiet_run(1, _mock_cfg(), ["office"]), _mock_cfg())
        self.assertIn("| host_cpu_peak_pct |", summary)

    def test_new_run_dir_never_reuses_a_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = {new_run_dir(Path(tmp)) for _ in range(3)}
            self.assertEqual(len(dirs), 3)


if __name__ == "__main__":
    unittest.main()
