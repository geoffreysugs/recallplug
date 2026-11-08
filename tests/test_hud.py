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


class HudWorkAreaTests(unittest.TestCase):
    """hud_geometry with a faked work area (no real screen is read)."""
    M = hud.WORK_AREA_MARGIN

    def place(self, area, wanted=200, screen=(1920, 1080)):
        w, h, x, y = hud.hud_geometry(wanted, *screen, work_area=area)
        return w, h, x, y, x + w, y + h

    def assertInside(self, area, right, bottom, x, y):
        self.assertEqual(right, area[2] - self.M)
        self.assertEqual(bottom, area[3] - self.M)
        self.assertGreaterEqual(x, area[0])
        self.assertGreaterEqual(y, area[1])

    def test_normal_taskbar_at_the_bottom(self):
        area = (0, 0, 1920, 1032)                         # 48 px taskbar
        w, h, x, y, right, bottom = self.place(area)
        self.assertEqual((w, h, x, y), (440, 200, 1472, 824))
        self.assertInside(area, right, bottom, x, y)

    def test_taller_taskbar(self):
        area = (0, 0, 1920, 960)                          # 120 px, two rows
        w, h, x, y, right, bottom = self.place(area)
        self.assertEqual(bottom, 952)                     # the old fixed margin gave 1012
        self.assertInside(area, right, bottom, x, y)

    def test_taskbar_on_the_right(self):
        area = (0, 0, 1860, 1080)
        w, h, x, y, right, bottom = self.place(area)
        self.assertEqual((x, right, bottom), (1412, 1852, 1072))
        self.assertInside(area, right, bottom, x, y)

    def test_taskbar_on_top_or_left_keeps_the_corner(self):
        for area in ((0, 40, 1920, 1080), (60, 0, 1920, 1080)):
            _w, _h, x, y, right, bottom = self.place(area)
            self.assertInside(area, right, bottom, x, y)

    def test_grows_upward_and_stays_in_the_work_area(self):
        area = (0, 0, 1920, 1032)
        bottoms = {self.place(area, wanted)[5] for wanted in (50, 200, 400, 5000)}
        self.assertEqual(bottoms, {1032 - self.M})
        _w, h, _x, y, _r, _b = self.place(area, 5000)
        self.assertEqual(h, 540)                          # still half the screen at most
        small = (0, 0, 1920, 400)                         # tiny work area: height is capped
        _w, h, _x, y, _r, bottom = self.place(small, 5000)
        self.assertEqual((y, bottom), (self.M, 400 - self.M))

    def test_falls_back_to_the_old_rule(self):
        old = hud.hud_geometry(200, 1920, 1080)
        for bad in (None, (0, 0, 0, 0), (0, 0, 5000, 1080), (10, 10, 5, 5), "x", (1, 2, 3)):
            self.assertEqual(hud.hud_geometry(200, 1920, 1080, work_area=bad), old, bad)
        self.assertEqual(old[3] + old[1], 1080 - hud.TASKBAR_MARGIN)


class WorkAreaApiTests(unittest.TestCase):
    def test_not_windows_gives_none(self):
        from app import win_api
        with mock.patch.object(win_api, "IS_WINDOWS", False):
            self.assertIsNone(win_api.get_work_area())

    def test_failed_call_gives_none(self):
        from app import win_api
        if not win_api.IS_WINDOWS:
            self.skipTest("needs Windows")
        user32 = mock.Mock()
        user32.SystemParametersInfoW.return_value = 0
        with mock.patch.object(win_api.ctypes, "windll", mock.Mock(user32=user32)):
            self.assertIsNone(win_api.get_work_area())

    def test_show_hud_uses_the_work_area(self):
        import inspect
        self.assertIn("get_work_area()", inspect.getsource(hud.show_hud))


if __name__ == "__main__":
    unittest.main()
