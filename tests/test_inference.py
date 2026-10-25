import contextlib
import io
import unittest

from app.config import Config
from app.inference import InferenceBackend, get_backend
from app.inference.hailo_backend import HailoBackend
from app.inference.mock_backend import MockBackend
from app.inference.ollama_backend import OllamaBackend
from app.summarizer import build_prompt, condense_prompt, summarize
from app.titles import Entry


def _pick(name):
    cfg = Config()
    cfg.backend = name
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        backend = get_backend(cfg)
    return backend, out.getvalue()


class FactoryTests(unittest.TestCase):
    def test_default_is_ollama(self):
        backend, notice = _pick("ollama")
        self.assertIsInstance(backend, OllamaBackend)
        self.assertEqual(notice, "")

    def test_mock_is_used_when_chosen(self):
        backend, notice = _pick("mock")
        self.assertIsInstance(backend, MockBackend)
        self.assertEqual(notice, "")

    def test_unhealthy_hailo_falls_back_to_ollama_with_one_line_notice(self):
        backend, notice = _pick("hailo")
        self.assertIsInstance(backend, OllamaBackend)
        self.assertEqual(notice.count("\n"), 1)
        self.assertIn("hailo", notice)

    def test_unknown_name_falls_back_to_ollama(self):
        backend, notice = _pick("tpu9000")
        self.assertIsInstance(backend, OllamaBackend)
        self.assertIn("unknown backend", notice)

    def test_all_backends_implement_the_interface(self):
        for cls in (OllamaBackend, HailoBackend, MockBackend):
            self.assertTrue(issubclass(cls, InferenceBackend))


class BackendTests(unittest.TestCase):
    def test_hailo_stub(self):
        hailo = HailoBackend(Config())
        self.assertFalse(hailo.health())
        with self.assertRaises(NotImplementedError) as ctx:
            hailo.generate_text("hi")
        self.assertEqual(str(ctx.exception), "planned for Stage II")

    def test_ollama_health_false_when_nothing_listens(self):
        cfg = Config()
        cfg.ollama_url = "http://127.0.0.1:9/api/generate"
        self.assertFalse(OllamaBackend(cfg).health())

    def test_mock_answer_has_work_and_next_lines(self):
        entries = [Entry(1, "Q3 Supplier.txt", "Notepad"), Entry(2, "Contract Draft v2.txt", "Notepad")]
        text, metrics = MockBackend(Config()).generate_text(build_prompt(entries))
        self.assertEqual(text, "Work: You were working on Contract tasks.\n"
                               "Next: pick up where you left off in Contract Draft v2.txt (Notepad)")
        self.assertIn("MEASURED", metrics["label"])

    def test_mock_condense_answer_is_short(self):
        long = "Next: " + "word " * 40
        text, _ = MockBackend(Config()).generate_text(condense_prompt(long.strip()))
        self.assertLessEqual(len(text), 78)
        self.assertTrue(text.startswith("Next: word"))

    def test_summarize_with_mock_backend_passes_checks(self):
        cfg = Config()
        cfg.metrics_csv = ""
        entries = [Entry(1, "Q3 Supplier.txt", "Notepad"), Entry(2, "Contract Draft v2.txt", "Notepad")]
        result = summarize(entries, cfg, MockBackend(cfg))
        self.assertEqual(result["source"], "model")
        self.assertEqual(result["metrics"]["bullets_replaced"], 0)


if __name__ == "__main__":
    unittest.main()
