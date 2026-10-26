"""Pure parts of tools/vision_eval.py only: no network, no Ollama, no images."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import vision_eval as ve


def _row(mode="cpu", image="S1", warmup=False, wall=10.0, peak=40.0, vram=0, valid=None,
         description="A text document with a table of freight quotes.", error=""):
    row = {k: "" for k in ve.CSV_FIELDS}
    ok, types, topics = ("", [], []) if warmup else ve.check_expected(description, image)
    row.update({"mode": mode, "mode_name": ve.MODES[mode][0], "image": image, "warmup": warmup,
                "description": description, "likely_correct": ok,
                "type_hits": ";".join(types), "topic_hits": ";".join(topics),
                "copied_digits": ve.copied_digits(description), "wall_s": wall,
                "total_duration_s": wall - 0.5, "cpu_avg_pct": peak / 2, "cpu_peak_pct": peak,
                "ram_peak_pct": 60.0, "ollama_rss_peak_mb": 3000, "size_mb": 4000,
                "size_vram_mb": vram, "size_vram_ok": ve.size_vram_ok(mode, vram),
                "error": error, "label": ve.LABEL})
    row["invalid_reason"] = ve.invalid_reason(mode, vram, error, None if warmup else description)
    row["valid"] = not row["invalid_reason"] if valid is None else valid
    return row


class KeywordTests(unittest.TestCase):
    def test_case_insensitive_and_plurals(self):
        self.assertEqual(ve.keyword_hits("Freight QUOTES from carriers", ["quote", "carrier", "rocket"]),
                         ["quote", "carrier"])

    def test_keyword_must_start_a_word(self):
        self.assertEqual(ve.keyword_hits("The context of the rest", ["text"]), [])
        self.assertEqual(ve.keyword_hits("Plain text file", ["text"]), ["text"])

    def test_multi_word_keywords(self):
        self.assertEqual(ve.keyword_hits("A Web Page about air quality", ["web page", "air quality"]),
                         ["web page", "air quality"])

    def test_likely_correct_needs_type_and_topic(self):
        self.assertEqual(ve.check_expected("A legal contract about payment terms.", "S2"),
                         (True, ["contract", "legal"], ["payment", "terms"]))
        self.assertFalse(ve.check_expected("A legal contract.", "S2")[0])           # no topic
        self.assertFalse(ve.check_expected("Something about payment.", "S2")[0])    # no type
        self.assertTrue(ve.check_expected("A Wikipedia article on the Tsiolkovsky rocket equation.",
                                          "S4")[0])
        self.assertTrue(ve.check_expected("A Canva slide listing air quality goals.", "S5")[0])

    def test_expectations_are_the_pre_registered_ones(self):
        self.assertEqual(set(ve.EXPECTED), {"S1", "S2", "S3", "S4", "S5"})
        self.assertEqual(ve.EXPECTED["S3"]["topic"], ["action", "task", "project", "status", "sync", "schedule"])
        self.assertEqual(ve.EXPECTED["S4"]["type"],
                         ["wikipedia", "encyclopedia", "article", "web page", "webpage"])


class CopiedDigitsTests(unittest.TestCase):
    def test_digits_and_emails(self):
        self.assertTrue(ve.copied_digits("A quote for 3 carriers."))
        self.assertTrue(ve.copied_digits("Contact someone@example.com for details."))
        self.assertTrue(ve.copied_digits("Q3 forecast"))
        self.assertFalse(ve.copied_digits("A contract about payment terms and delivery."))
        self.assertFalse(ve.copied_digits("Send it @ noon to the team"))


class ValidityTests(unittest.TestCase):
    def test_cpu_only_needs_zero_vram(self):
        self.assertTrue(ve.size_vram_ok("cpu", 0))
        self.assertFalse(ve.size_vram_ok("cpu", 1024))
        self.assertFalse(ve.size_vram_ok("cpu", None))          # model not in /api/ps: cannot tell
        self.assertTrue(ve.call_valid("cpu", 0))
        self.assertFalse(ve.call_valid("cpu", 512))
        self.assertFalse(ve.call_valid("cpu", 0, "TimeoutError: timed out"))

    def test_gpu_mode(self):
        self.assertTrue(ve.size_vram_ok("gpu", 2048))
        self.assertFalse(ve.size_vram_ok("gpu", 0))
        self.assertTrue(ve.call_valid("gpu", 0))                 # only flagged, not invalid
        self.assertFalse(ve.call_valid("gpu", 2048, "URLError"))

    def test_vram_cell(self):
        self.assertEqual(ve.vram_cell(_row("cpu", vram=0)), "yes")
        self.assertEqual(ve.vram_cell(_row("cpu", vram=900)), "INVALID (900 MB in VRAM)")
        self.assertEqual(ve.vram_cell(_row("gpu", vram=2500)), "yes (2500 MB)")
        self.assertEqual(ve.vram_cell(_row("gpu", vram=0)), "WARN: 0 MB in VRAM")


class OllamaProcessTests(unittest.TestCase):
    def test_counts_ollama_and_llama_server(self):
        for name in ["ollama.exe", "Ollama", "ollama app.exe", "OLLAMA_LLAMA_SERVER.EXE",
                     "llama-server", "llama-server.exe", "LLAMA-SERVER.EXE", "ggml-llama-server"]:
            self.assertTrue(ve.is_ollama_process(name), name)

    def test_other_processes_are_not_counted(self):
        for name in ["chrome.exe", "python.exe", "llama.exe", "llamaserver.exe", "my-ollama.exe", "", None]:
            self.assertFalse(ve.is_ollama_process(name), name)


class GarbledTests(unittest.TestCase):
    def test_garbled(self):
        for text in ["@" * 80, "  @@@@@@@@  ", "", "   ", "ab", "abababab", "aaaaaaab c",
                     "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!? ok"]:
            self.assertTrue(ve.garbled(text), repr(text))

    def test_normal_text_is_not_garbled(self):
        for text in ["The screen shows a document titled Meeting Notes with action items.",
                     "OK.", "abc"]:
            self.assertFalse(ve.garbled(text), repr(text))

    def test_share_limit_is_more_than_60_percent(self):
        self.assertFalse(ve.garbled("aaabc"))          # 60% exactly is still fine
        self.assertTrue(ve.garbled("aaaabc"))          # 66.7%

    def test_garbled_call_is_invalid_in_both_modes(self):
        self.assertEqual(ve.invalid_reason("gpu", 2048, "", "@" * 80), "garbled output")
        self.assertEqual(ve.invalid_reason("cpu", 0, "", "@" * 80), "garbled output")
        self.assertEqual(ve.invalid_reason("cpu", 0, "", "A contract about payment terms."), "")
        self.assertEqual(ve.invalid_reason("cpu", 900, "", "A contract."), "size_vram not 0")
        self.assertEqual(ve.invalid_reason("cpu", None, "", "A contract."), "size_vram unknown")
        self.assertEqual(ve.invalid_reason("gpu", 2048, "URLError", ""), "error")
        self.assertEqual(ve.invalid_reason("gpu", 2048, "", None), "")   # warm-up: no garbled check

    def test_garbled_calls_are_excluded_from_stats_and_decision(self):
        rows = [_row("cpu", f"S{i}", wall=5, peak=10) for i in range(1, 6)]
        rows[1] = _row("cpu", "S2", wall=5, peak=10, description="@" * 80)
        s = ve.mode_stats(rows, "cpu")
        self.assertEqual((s["valid_calls"], s["invalid_calls"]), (4, 1))
        self.assertEqual(s["invalid_reasons"], {"garbled output": 1})
        self.assertEqual(ve.decision(rows), ("UNDETERMINED", "no valid CPU-only call for S2"))
        text = ve.format_report(rows, {}, "m", "t")
        self.assertIn("Excluded as INVALID: 1 of 5 (1 garbled output).", text)
        self.assertIn("INVALID (garbled output)", text)


class WarmupTests(unittest.TestCase):
    def test_warmup_is_text_only_and_not_a_measured_image(self):
        self.assertEqual(ve.WARMUP_PROMPT, "Say OK.")
        self.assertNotIn(ve.WARMUP_IMAGE, ve.IMAGES)
        sent = []

        def fake_post(path, body, timeout):
            sent.append(body)
            return {"response": "OK.", "total_duration": 10**9}

        with mock.patch.object(ve, "_post", fake_post), \
                mock.patch.object(ve, "loaded", return_value={"size": 2**30, "size_vram": 0}), \
                mock.patch.object(ve, "Sampler") as sampler:
            sampler.return_value.__enter__.return_value = mock.Mock(avg=1.0, peak=2.0, ram_peak=3.0,
                                                                    rss_peak=2**20)
            row = ve.one_call("qwen2.5vl:3b", "cpu", ve.WARMUP_IMAGE, None)
        self.assertEqual(len(sent), 1)
        self.assertNotIn("images", sent[0])
        self.assertEqual(sent[0]["prompt"], "Say OK.")
        self.assertEqual(sent[0]["options"]["num_gpu"], 0)
        self.assertTrue(row["warmup"])
        self.assertTrue(row["valid"])                    # "OK." is short but not garbled-checked
        self.assertEqual(row["likely_correct"], "")

    def test_measured_call_sends_the_image(self):
        sent = []

        def fake_post(path, body, timeout):
            sent.append(body)
            return {"response": "@" * 80}

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp, "S1.png")
            img.write_bytes(b"not really a png")
            with mock.patch.object(ve, "_post", fake_post), \
                    mock.patch.object(ve, "loaded", return_value={"size": 2**30, "size_vram": 2**30}), \
                    mock.patch.object(ve, "Sampler") as sampler:
                sampler.return_value.__enter__.return_value = mock.Mock(avg=1.0, peak=2.0,
                                                                        ram_peak=3.0, rss_peak=0)
                row = ve.one_call("qwen2.5vl:3b", "gpu", "S1", img)
        self.assertEqual(sent[0]["prompt"], ve.PROMPT)
        self.assertEqual(len(sent[0]["images"]), 1)
        self.assertNotIn("num_gpu", sent[0]["options"])
        self.assertEqual((row["valid"], row["invalid_reason"]), (False, "garbled output"))


class DecisionTests(unittest.TestCase):
    def _cpu(self, values):
        return [_row("cpu", f"S{i}", wall=w, peak=p) for i, (w, p) in enumerate(values, 1)]

    def test_pass(self):
        result, why = ve.decision(self._cpu([(12, 40), (15, 49.9), (9, 30), (99, 99), (99, 99)]))
        self.assertEqual(result, ve.DECISION_PASS)
        self.assertIn("S2: 15 s, CPU peak 49.9%", why)

    def test_too_slow_or_too_busy(self):
        self.assertEqual(ve.decision(self._cpu([(15.1, 10), (5, 10), (5, 10)]))[0], ve.DECISION_FAIL)
        self.assertEqual(ve.decision(self._cpu([(5, 50), (5, 10), (5, 10)]))[0], ve.DECISION_FAIL)

    def test_only_s1_s2_s3_and_cpu_mode_count(self):
        rows = self._cpu([(5, 10), (5, 10), (5, 10), (60, 95), (60, 95)])
        rows += [_row("gpu", "S1", wall=99, peak=99), _row("cpu", "S1", warmup=True, wall=99, peak=99)]
        self.assertEqual(ve.decision(rows)[0], ve.DECISION_PASS)

    def test_invalid_or_missing_is_undetermined(self):
        rows = self._cpu([(5, 10), (5, 10)])
        self.assertEqual(ve.decision(rows), ("UNDETERMINED", "no valid CPU-only call for S3"))
        rows = self._cpu([(5, 10), (5, 10), (5, 10)])
        rows[1] = _row("cpu", "S2", vram=700)
        self.assertEqual(ve.decision(rows)[0], "UNDETERMINED")

    def test_rule_text_is_the_pre_registered_one(self):
        self.assertEqual(ve.DECISION_RULE,
                         "all three <= 15 s AND CPU peak < 50%  -> away-time capture does not need "
                         "the accelerator; otherwise -> CPU works but interferes, supports moving "
                         "vision inference off the host")


class FormattingTests(unittest.TestCase):
    def _rows(self):
        rows = []
        for mode in ("gpu", "cpu"):
            vram = 2500 if mode == "gpu" else 0
            rows.append(_row(mode, ve.WARMUP_IMAGE, warmup=True, vram=vram, description="OK."))
            rows += [_row(mode, f"S{i}", vram=vram) for i in range(1, 6)]
        return rows

    def test_report_contents(self):
        info = {"CPU": "Test CPU", "RAM": "16.0 GB", "Windows": "Windows-11", "Ollama": "0.0.1"}
        loaded = {"gpu": [], "cpu": [{"name": "qwen2.5:1.5b", "size": 2**30, "size_vram": 0}]}
        text = ve.format_report(self._rows(), info, "qwen2.5vl:3b", "2026-10-26 12:00:00", loaded)
        for needle in ["Qwen2.5-VL 3B", "Qwen2-VL-2B", "one machine", "MEASURED (host)",
                       "## RTX 4060 Laptop (MEASURED (host))",
                       "## CPU only (num_gpu=0) (MEASURED (host))",
                       "| Test CPU |", "| 16.0 GB |", "| Windows-11 |", "| 0.0.1 |",
                       ve.TABLE_HEAD, ve.DECISION_RULE,
                       "Warm-up, warm-up (text only) (excluded from the statistics)",
                       "Models loaded at the start of this mode (GET /api/ps, before the warm-up): none",
                       "before the warm-up): qwen2.5:1.5b (1024 MB, VRAM 0 MB)",
                       "Excluded as INVALID: 0 of 5.",
                       "type: contract, agreement, document, legal; topic: payment",
                       f"**Result: {ve.DECISION_PASS}**"]:
            self.assertIn(needle, text)
        self.assertEqual(text.count("| S3 |"), 2)               # one row per mode

    def test_invalid_cpu_calls_are_called_out(self):
        rows = self._rows()
        rows[-1] = _row("cpu", "S5", vram=800)
        text = ve.format_report(rows, {}, "m", "t")
        self.assertIn("INVALID (800 MB in VRAM)", text)
        self.assertIn("Excluded as INVALID: 1 of 5 (1 size_vram not 0).", text)
        self.assertIn("Models loaded at the start of this mode (GET /api/ps, before the warm-up): unknown",
                      text)
        self.assertEqual(ve.mode_stats(rows, "cpu")["invalid_calls"], 1)

    def test_mode_stats_exclude_warmup(self):
        s = ve.mode_stats(self._rows(), "gpu")
        # the fake freight-quote description only fits S1's expectations
        self.assertEqual((s["measured_calls"], s["valid_calls"], s["likely_correct"]), (5, 5, 1))
        self.assertEqual(s["mode_name"], "RTX 4060 Laptop")

    def test_md_cell_and_fmt(self):
        self.assertEqual(ve.md_cell("a | b\nc"), "a \\| b c")
        self.assertEqual(ve.fmt(True), "yes")
        self.assertEqual(ve.fmt(""), "n/a")
        self.assertEqual(ve.fmt(12.0), "12")
        self.assertEqual(ve.fmt(3.14159), "3.14")


class PathTests(unittest.TestCase):
    def test_refuses_the_repository(self):
        self.assertTrue(ve.is_inside(ve.PROJECT_ROOT / "bench" / "x", ve.PROJECT_ROOT))
        self.assertFalse(ve.is_inside(Path(r"D:\AI\test\results"), ve.PROJECT_ROOT))

    def test_find_images_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("s1.PNG", "S2.png", "s3.png", "S4.Png", "S5.png", "S6.png", "notes.txt"):
                Path(tmp, name).write_bytes(b"")
            self.assertEqual(sorted(ve.find_images(Path(tmp))), ["S1", "S2", "S3", "S4", "S5"])

    def test_same_model(self):
        self.assertTrue(ve.same_model("qwen2.5vl:3b", "qwen2.5vl:3b"))
        self.assertTrue(ve.same_model("llava", "llava:latest"))
        self.assertFalse(ve.same_model("qwen2.5:1.5b", "qwen2.5vl:3b"))

    def test_main_refuses_output_inside_repo(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ve.main(["--images", "x", "--out", str(ve.PROJECT_ROOT / "results")])
        self.assertEqual(code, 2)
        self.assertIn("refusing to write inside the repository", out.getvalue())


if __name__ == "__main__":
    unittest.main()
