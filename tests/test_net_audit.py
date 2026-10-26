"""Only the pure parts are tested; nothing here opens a network connection
or starts a process."""
import contextlib
import io
import unittest
from unittest import mock

from tools import net_audit
from tools.net_audit import classify, exit_code, format_report, summarise


class ClassifyTests(unittest.TestCase):
    def test_loopback(self):
        for ip in ["127.0.0.1", "127.5.6.7", "127.255.255.254", "::1", "::ffff:127.0.0.1"]:
            self.assertEqual(classify(ip), "loopback", ip)

    def test_external(self):
        for ip in ["128.0.0.1", "126.255.255.255", "192.168.1.10", "10.0.0.5", "8.8.8.8",
                   "0.0.0.0", "::", "2001:4860:4860::8888", "fe80::1%12", "::ffff:8.8.8.8"]:
            self.assertEqual(classify(ip), "external", ip)

    def test_garbage_counts_as_external(self):
        for ip in ["", "localhost", "not-an-ip", "999.1.1.1"]:
            self.assertEqual(classify(ip), "external", ip)


class SummaryTests(unittest.TestCase):
    SAMPLES = [
        {"recallplug": [("127.0.0.1", 11434)], "ollama": [("127.0.0.1", 50001)]},
        {"recallplug": [], "ollama": []},
        {"recallplug": [("127.0.0.1", 11434), ("::1", 11434)],
         "ollama": [("127.0.0.1", 50002), ("34.120.1.2", 443)]},
    ]

    def test_counts_addresses_and_peak(self):
        s = summarise(self.SAMPLES)
        self.assertEqual(s["samples"], 3)
        self.assertEqual(s["recallplug"]["counts"], {"loopback": 3, "external": 0})
        self.assertEqual(s["recallplug"]["addresses"]["loopback"], ["127.0.0.1:11434", "[::1]:11434"])
        self.assertEqual(s["recallplug"]["peak"], 2)
        self.assertEqual(s["ollama"]["counts"], {"loopback": 2, "external": 1})
        self.assertEqual(s["ollama"]["addresses"]["external"], ["34.120.1.2:443"])

    def test_ollama_external_does_not_fail_the_audit(self):
        self.assertEqual(exit_code(summarise(self.SAMPLES)), 0)

    def test_recallplug_external_fails_the_audit(self):
        samples = self.SAMPLES + [{"recallplug": [("93.184.216.34", 443)]}]
        s = summarise(samples)
        self.assertEqual(s["recallplug"]["counts"]["external"], 1)
        self.assertEqual(exit_code(s), 1)

    def test_no_samples(self):
        s = summarise([])
        self.assertEqual((s["samples"], s["recallplug"]["peak"], exit_code(s)), (0, 0, 0))

    def test_report_has_counts_addresses_and_labels_only(self):
        text = format_report(summarise(self.SAMPLES), 10, "2026-10-26 10:00:00")
        self.assertIn("samples (1 per second): 3", text)
        self.assertIn("Ollama processes (informational, not part of the verdict):", text)
        self.assertIn("external addresses: 34.120.1.2:443", text)
        self.assertIn("PASS: no external connection from the RecallPlug process tree", text)
        self.assertIn("can miss a connection", text)

    def test_failing_report_says_fail(self):
        text = format_report(summarise([{"recallplug": [("8.8.8.8", 53)]}]), 1, "t")
        self.assertIn("FAIL", text)
        self.assertIn("external addresses: 8.8.8.8:53", text)


class MissingPsutilTests(unittest.TestCase):
    def test_prints_install_hint_and_exits(self):
        out = io.StringIO()
        with mock.patch.object(net_audit, "psutil", None), \
                mock.patch.object(net_audit, "run_audit") as run, contextlib.redirect_stdout(out):
            code = net_audit.main(["--minutes", "1"])
        self.assertEqual(code, 2)
        self.assertIn("pip install psutil", out.getvalue())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
