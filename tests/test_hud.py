import json
import subprocess
import unittest
from unittest import mock

from app import hud

TITLE = "Welcome back - away 5 min"
BULLETS = ["You stopped in 合約草稿 v2.txt (Notepad)",
           "You were comparing supplier quotes.",
           "Next: finish the 報價 table in Q3 Forecast.xlsx"]


class _FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    def close(self):
        self.closed = True


class HudLaunchTests(unittest.TestCase):
    def _launch(self):
        proc = mock.Mock()
        proc.stdin = _FakeStdin()
        with mock.patch.object(hud.subprocess, "Popen", return_value=proc) as popen:
            hud.launch_hud(TITLE, BULLETS, 15)
        return popen, proc

    def test_command_line_has_no_recap_text(self):
        popen, _ = self._launch()
        argv = popen.call_args.args[0]
        self.assertEqual(argv[1:], ["-m", "app.hud", "--stdin"])
        joined = " ".join(argv)
        for text in [TITLE, *BULLETS, "合約", "報價", "Welcome", "Next:", "supplier"]:
            self.assertNotIn(text, joined)
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)

    def test_recap_goes_to_stdin_as_one_utf8_json_object(self):
        _, proc = self._launch()
        self.assertTrue(proc.stdin.closed)
        data = json.loads(proc.stdin.data.decode("utf-8"))
        self.assertEqual(data, {"title": TITLE, "bullets": BULLETS, "seconds": 15})
        self.assertIn("合約草稿".encode("utf-8"), proc.stdin.data)      # real UTF-8, not \u escapes

    def test_payload_round_trips_with_chinese_text(self):
        raw = hud.encode_payload("歡迎回來 - 離開 5 分鐘", BULLETS, 20)
        self.assertEqual(hud.decode_payload(raw), ("歡迎回來 - 離開 5 分鐘", BULLETS, 20))

    def test_decode_payload_defaults(self):
        self.assertEqual(hud.decode_payload(b""), ("Welcome back", [], 15))
        self.assertEqual(hud.decode_payload(b'{"bullets": ["  a b  ", " "]}'),
                         ("Welcome back", ["a b"], 15))

    def test_a_dead_hud_does_not_break_the_caller(self):
        proc = mock.Mock()
        proc.stdin.write.side_effect = BrokenPipeError
        with mock.patch.object(hud.subprocess, "Popen", return_value=proc):
            hud.launch_hud(TITLE, BULLETS, 15)          # must not raise

    def test_sample_flags_still_work(self):
        for flag in ("--sample", "--sample-long"):
            with mock.patch.object(hud, "show_hud") as show, \
                    mock.patch("sys.argv", ["app.hud", flag]):
                hud.main()
            title, bullets, _seconds = show.call_args.args
            self.assertTrue(title.startswith("Welcome back"))
            self.assertEqual(len(bullets), 3)

    def test_stdin_flag_reads_the_payload(self):
        stdin = mock.Mock()
        stdin.buffer.read.return_value = hud.encode_payload(TITLE, BULLETS, 9)
        with mock.patch.object(hud, "show_hud") as show, \
                mock.patch("sys.argv", ["app.hud", "--stdin"]), mock.patch("sys.stdin", stdin):
            hud.main()
        self.assertEqual(show.call_args.args, (TITLE, BULLETS, 9))


if __name__ == "__main__":
    unittest.main()
