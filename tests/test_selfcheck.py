"""Start-up self-check against the fake Ollama test server (no real Ollama)."""
import contextlib
import io
import json
import socket
import threading
import unittest
from http.server import HTTPServer
from unittest import mock

from app import main as app_main
from app import selfcheck
from app.config import Config
from tests.test_core import _FakeOllama


class _TagsOllama(_FakeOllama):
    """The existing fake Ollama, plus GET /api/tags and POST /api/pull."""
    models = ["llama3:latest", "qwen2.5:1.5b"]
    pull_lines = [
        {"status": "pulling manifest"},
        {"status": "pulling abc", "digest": "abc", "total": 1000, "completed": 0},
        {"status": "pulling abc", "digest": "abc", "total": 1000, "completed": 500},
        {"status": "pulling abc", "digest": "abc", "total": 1000, "completed": 1000},
        {"status": "verifying sha256 digest"},
        {"status": "success"},
    ]
    pull_code = 200

    def do_GET(self):
        if self.path != "/api/tags":
            self.send_error(404)
            return
        data = json.dumps({"models": [{"name": n, "model": n} for n in self.models]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/api/pull":
            return super().do_POST()
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.pulls.append(body)
        if self.pull_code != 200:
            self.send_error(self.pull_code)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for item in self.pull_lines:
            self.wfile.write((json.dumps(item) + "\n").encode())
            self.wfile.flush()


class _NoModelOllama(_TagsOllama):
    models = ["llama3:latest"]


class _PullErrorOllama(_NoModelOllama):
    pull_lines = [{"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"}]


class _PullHttpErrorOllama(_NoModelOllama):
    pull_code = 500


class _PullCutOllama(_NoModelOllama):
    pull_lines = [{"status": "pulling abc", "total": 10, "completed": 3}]


class _GarbledTagsOllama(_TagsOllama):
    def do_GET(self):
        data = b"not json"
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@contextlib.contextmanager
def fake_ollama(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    server.prompts, server.pulls = [], []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = Config()
    cfg.metrics_csv = ""
    cfg.ollama_url = f"http://127.0.0.1:{server.server_port}/api/generate"
    try:
        yield cfg, server
    finally:
        server.shutdown()
        server.server_close()


def closed_port_cfg():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfg = Config()
    cfg.ollama_url = f"http://127.0.0.1:{port}/api/generate"
    return cfg


class CheckTests(unittest.TestCase):
    def test_ollama_down(self):
        r = selfcheck.check(closed_port_cfg())
        self.assertEqual((r.ollama_state, r.model_state), (selfcheck.NOT_RUNNING, selfcheck.UNKNOWN))
        self.assertEqual(r.model_name, "qwen2.5:1.5b")
        self.assertIn("https://ollama.com", r.hint)
        self.assertFalse(r.ok)

    def test_up_without_model(self):
        with fake_ollama(_NoModelOllama) as (cfg, _):
            r = selfcheck.check(cfg)
        self.assertEqual((r.ollama_state, r.model_state), (selfcheck.RUNNING, selfcheck.MISSING))
        self.assertIn("ollama pull qwen2.5:1.5b", r.hint)
        self.assertIn("about 1 GB", r.hint)

    def test_up_with_model(self):
        with fake_ollama(_TagsOllama) as (cfg, _):
            r = selfcheck.check(cfg)
        self.assertEqual((r.ollama_state, r.model_state), (selfcheck.RUNNING, selfcheck.PRESENT))
        self.assertTrue(r.ok)

    def test_model_without_tag_matches_latest(self):
        with fake_ollama(_TagsOllama) as (cfg, _):
            cfg.model = "llama3"
            self.assertEqual(selfcheck.check(cfg).model_state, selfcheck.PRESENT)

    def test_unreadable_model_list_is_unknown(self):
        with fake_ollama(_GarbledTagsOllama) as (cfg, _):
            r = selfcheck.check(cfg)
        self.assertEqual((r.ollama_state, r.model_state), (selfcheck.RUNNING, selfcheck.UNKNOWN))

    def test_only_loopback_hosts_are_contacted(self):
        cfg = Config()
        cfg.ollama_url = "http://example.com:11434/api/generate"
        with mock.patch("urllib.request.urlopen") as urlopen:
            r = selfcheck.check(cfg)
            p = selfcheck.ensure_model(cfg)
        urlopen.assert_not_called()
        self.assertEqual(r.ollama_state, selfcheck.NOT_RUNNING)
        self.assertFalse(p.ok)

    def test_default_url_is_local(self):
        self.assertEqual(selfcheck.base_url(Config()), "http://127.0.0.1:11434")


class PullTests(unittest.TestCase):
    def test_pull_succeeds_with_progress(self):
        seen = []
        with fake_ollama(_NoModelOllama) as (cfg, server):
            r = selfcheck.ensure_model(cfg, lambda p, s: seen.append((p, s)))
        self.assertTrue(r.ok)
        self.assertEqual(server.pulls, [{"model": "qwen2.5:1.5b", "stream": True}])
        self.assertEqual([p for p, _ in seen if p is not None], [0.0, 50.0, 100.0])
        self.assertEqual(seen[0], (None, "pulling manifest"))

    def test_pull_reports_error_line(self):
        with fake_ollama(_PullErrorOllama) as (cfg, _):
            r = selfcheck.ensure_model(cfg)
        self.assertFalse(r.ok)
        self.assertIn("file does not exist", r.message)

    def test_pull_http_error(self):
        with fake_ollama(_PullHttpErrorOllama) as (cfg, _):
            r = selfcheck.ensure_model(cfg)
        self.assertFalse(r.ok)
        self.assertIn("HTTP 500", r.message)

    def test_pull_stream_ends_early(self):
        with fake_ollama(_PullCutOllama) as (cfg, _):
            r = selfcheck.ensure_model(cfg)
        self.assertFalse(r.ok)

    def test_pull_ollama_down(self):
        r = selfcheck.ensure_model(closed_port_cfg())
        self.assertFalse(r.ok)
        self.assertIn("could not reach Ollama", r.message)

    def test_pull_ctrl_c(self):
        def interrupt(*_a, **_k):
            raise KeyboardInterrupt
        with fake_ollama(_NoModelOllama) as (cfg, _):
            with mock.patch("urllib.request.urlopen", interrupt):
                r = selfcheck.ensure_model(cfg)
        self.assertEqual(r, selfcheck.PullResult(False, "Download cancelled."))

    def test_broken_progress_callback_is_ignored(self):
        with fake_ollama(_NoModelOllama) as (cfg, _):
            r = selfcheck.ensure_model(cfg, lambda p, s: 1 / 0)
        self.assertTrue(r.ok)


class CliTests(unittest.TestCase):
    def run_check(self, cfg, answer="", assume_yes=False):
        out, asked = [], []

        def ask(prompt):
            asked.append(prompt)
            return answer
        code = selfcheck.run_cli_check(cfg, assume_yes=assume_yes, ask=ask, out=out.append)
        return code, out, asked

    def test_down_exits_non_zero_with_help(self):
        code, out, asked = self.run_check(closed_port_cfg())
        self.assertNotEqual(code, 0)
        text = "\n".join(out)
        self.assertIn("Ollama is not running", text)
        self.assertIn("https://ollama.com", text)
        self.assertIn("ollama serve", text)
        self.assertEqual(asked, [])

    def test_model_present_continues(self):
        with fake_ollama(_TagsOllama) as (cfg, _):
            self.assertEqual(self.run_check(cfg)[0], 0)

    def test_missing_model_asks_and_no_means_exit(self):
        with fake_ollama(_NoModelOllama) as (cfg, server):
            code, out, asked = self.run_check(cfg, answer="")
        self.assertEqual(asked, ["Download it now with Ollama? [y/N] "])
        self.assertNotEqual(code, 0)
        self.assertEqual(server.pulls, [])
        self.assertIn("qwen2.5:1.5b", out[0])
        self.assertIn("about 1 GB", out[0])

    def test_missing_model_yes_downloads(self):
        with fake_ollama(_NoModelOllama) as (cfg, server):
            code, out, _ = self.run_check(cfg, answer="y")
        self.assertEqual(code, 0)
        self.assertEqual(len(server.pulls), 1)
        self.assertIn("100.0%", "\n".join(out))

    def test_assume_yes_does_not_ask(self):
        with fake_ollama(_NoModelOllama) as (cfg, server):
            code, _, asked = self.run_check(cfg, assume_yes=True)
        self.assertEqual((code, asked, len(server.pulls)), (0, [], 1))

    def test_failed_download_exits_non_zero(self):
        with fake_ollama(_PullErrorOllama) as (cfg, _):
            code, out, _ = self.run_check(cfg, answer="yes")
        self.assertNotEqual(code, 0)
        self.assertIn("Download failed", out[-1])

    def test_eof_at_prompt_means_no(self):
        def ask(_p):
            raise EOFError
        with fake_ollama(_NoModelOllama) as (cfg, _):
            code = selfcheck.run_cli_check(cfg, ask=ask, out=lambda s: None)
        self.assertNotEqual(code, 0)


class MainWiringTests(unittest.TestCase):
    def run_main(self, argv, check_code=0):
        with mock.patch.object(app_main, "run_live") as live, \
             mock.patch.object(app_main, "run_preview") as preview, \
             mock.patch.object(app_main, "enable_dpi_awareness"), \
             mock.patch.object(app_main, "start_pause_hotkey", create=True), \
             mock.patch.object(selfcheck, "run_cli_check", return_value=check_code) as chk, \
             contextlib.redirect_stdout(io.StringIO()):
            app_main.main(argv)
        return live, preview, chk

    def test_mock_backend_and_no_model_skip_the_check(self):
        for argv in (["--backend", "mock"], ["--no-model"]):
            live, _, chk = self.run_main(argv)
            chk.assert_not_called()
            live.assert_called_once()

    def test_default_runs_the_check(self):
        live, _, chk = self.run_main(["--demo"])
        chk.assert_called_once()
        self.assertFalse(chk.call_args.kwargs["assume_yes"])
        live.assert_called_once()

    def test_yes_flag_is_passed(self):
        _, preview, chk = self.run_main(["--preview", "5", "--yes"])
        self.assertTrue(chk.call_args.kwargs["assume_yes"])
        preview.assert_called_once()

    def test_failed_check_exits_without_running(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main([], check_code=2)
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
