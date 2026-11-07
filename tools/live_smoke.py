"""Live smoke test on a real Windows desktop (NOT part of unittest).

    python -B -m tools.live_smoke

Steps (each PASS / FAIL / SKIP, printed as a table at the end):
  1. self-check against the real local Ollama (tags, model present, pull of the
     already-installed model through the app's streaming pull routine)
  2. the real recap window, launched through app.hud.launch_hud like the app does:
     found with EnumWindows, visible, topmost, bottom-right of the work area,
     and closes by itself
  3. the real Ctrl+Alt+P hotkey wrapper driving a RecallEngine with FAKE title and
     idle providers; the key is pressed with SendInput
  4. a RecallEngine with fake titles and a fake idle provider, using the real Ollama:
     a recap_ready event with 1-3 lines must arrive within 60 s

It never reads or prints your real window titles (step 2 only looks for the
window named "RecallPlug HUD") and writes no files (metrics are switched off;
run with -B so Python writes no bytecode either). Windows will flash and a
beep may play while it runs.
Exit code: 1 if any step FAILs, else 0.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time

from app import selfcheck
from app.config import Config
from app.win_api import enable_dpi_awareness

IS_WINDOWS = sys.platform == "win32"
HUD_TITLE = "RecallPlug HUD"
HUD_SECONDS = 8
FAKE_TITLES = ("Supplier Quotes.txt - Notepad", "Meeting Notes.txt - Notepad",
               "Contract Draft v2.txt - Notepad")

results: list[tuple[str, str, str]] = []


def record(step: str, status: str, details: str) -> None:
    results.append((step, status, details))
    print(f"[{status}] {step}: {details}", flush=True)


def smoke_cfg() -> Config:
    cfg = Config()
    cfg.metrics_csv = ""        # this script writes no files
    return cfg


# ------------------------------------------------------------ Win32 helpers
if IS_WINDOWS:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                             wintypes.UINT]
    GWL_EXSTYLE = -20
    WS_EX_TOPMOST = 0x00000008
    SPI_GETWORKAREA = 0x0030

    def find_windows(title: str) -> list[int]:
        """Top-level windows whose title is exactly `title` (no other title is kept)."""
        found: list[int] = []

        def cb(hwnd, _lparam):
            n = user32.GetWindowTextLengthW(hwnd)
            if n == len(title):
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == title:
                    found.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return found

    def window_rect(hwnd) -> tuple[int, int, int, int]:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def work_area() -> tuple[int, int, int, int]:
        r = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
        return r.left, r.top, r.right, r.bottom

    # ---- SendInput
    ULONG_PTR = ctypes.c_size_t
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL, VK_MENU = 0x11, 0x12

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT

    def press_ctrl_alt_p() -> int:
        """Ctrl down, Alt down, P down, P up, Alt up, Ctrl up. Returns events sent."""
        seq = [(VK_CONTROL, 0), (VK_MENU, 0), (ord("P"), 0),
               (ord("P"), KEYEVENTF_KEYUP), (VK_MENU, KEYEVENTF_KEYUP),
               (VK_CONTROL, KEYEVENTF_KEYUP)]
        arr = (INPUT * len(seq))()
        for i, (vk, flags) in enumerate(seq):
            arr[i].type = INPUT_KEYBOARD
            arr[i].ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return int(user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT)))


def wait_until(cond, timeout: float, step: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


# ------------------------------------------------------------ step 1
def step_selfcheck() -> bool:
    """Returns True if Ollama is running (steps 4 needs it)."""
    name = "1 self-check (real Ollama)"
    cfg = smoke_cfg()
    r = selfcheck.check(cfg)
    if r.ollama_state != selfcheck.RUNNING:
        record(name, "SKIP", f"Ollama is not running at {selfcheck.base_url(cfg)} ({r.hint})")
        return False
    if r.model_state != selfcheck.PRESENT:
        record(name, "FAIL", f"GET /api/tags works, but model {r.model_name} is {r.model_state}")
        return True
    calls = {"n": 0, "percent": 0, "statuses": []}

    def progress(percent, status):
        calls["n"] += 1
        if percent is not None:
            calls["percent"] += 1
        if status and status not in calls["statuses"]:
            calls["statuses"].append(status)

    t0 = time.monotonic()
    pulled = selfcheck.ensure_model(cfg, progress)
    took = time.monotonic() - t0
    detail = (f"tags OK, {r.model_name} PRESENT; pull -> ok={pulled.ok} '{pulled.message}' "
              f"in {took:.1f}s, {calls['n']} progress lines ({calls['percent']} with percent), "
              f"statuses: {', '.join(s[:30] for s in calls['statuses'][:6])}")
    ok = pulled.ok and calls["n"] > 0 and took < 120
    record(name, "PASS" if ok else "FAIL", detail)
    return True


# ------------------------------------------------------------ step 2
def step_hud() -> None:
    name = "2 recap window"
    if not IS_WINDOWS:
        record(name, "SKIP", "needs Windows")
        return
    from app.hud import launch_hud

    if find_windows(HUD_TITLE):
        record(name, "FAIL", "a RecallPlug HUD window is already open; close it and rerun")
        return
    t0 = time.monotonic()
    launch_hud("Welcome back - smoke test",
               ["You stopped in Sample Plan.xlsx (Excel)",
                "You were checking sample supplier prices.",
                "Next: finish the price table in Sample Plan.xlsx"], HUD_SECONDS)
    if not wait_until(lambda: find_windows(HUD_TITLE), 15):
        record(name, "FAIL", "no top-level window titled 'RecallPlug HUD' appeared within 15 s")
        return
    appeared = time.monotonic() - t0
    time.sleep(0.5)                                  # let Tk finish placing it
    hwnds = find_windows(HUD_TITLE)
    if not hwnds:
        record(name, "FAIL", "the window vanished right after it appeared")
        return
    hwnd = hwnds[0]
    visible = bool(user32.IsWindowVisible(hwnd))
    topmost = bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)
    left, top, right, bottom = window_rect(hwnd)
    wl, wt, wr, wb = work_area()
    inside = wl <= left and right <= wr and wt <= top and bottom <= wb
    bottom_right = left >= wl + (wr - wl) // 2 and bottom >= wt + (wb - wt) // 2 and wr - right <= 60
    closed = wait_until(lambda: not find_windows(HUD_TITLE), HUD_SECONDS + 10)
    gone_after = time.monotonic() - t0
    detail = (f"appeared after {appeared:.1f}s; rect (L,T,R,B)=({left},{top},{right},{bottom}) "
              f"{right - left}x{bottom - top}; work area=({wl},{wt},{wr},{wb}); "
              f"visible={visible} topmost={topmost} inside_work_area={inside} "
              f"bottom_right={bottom_right}; closed by itself={closed} after {gone_after:.1f}s "
              f"(timeout {HUD_SECONDS}s)")
    ok = visible and topmost and inside and bottom_right and closed
    record(name, "PASS" if ok else "FAIL", detail)


# ------------------------------------------------------------ step 3
def step_hotkey() -> None:
    name = "3 Ctrl+Alt+P hotkey"
    if not IS_WINDOWS:
        record(name, "SKIP", "needs Windows")
        return
    from app.engine import RecallEngine
    from app.hotkey import GlobalHotkey

    cfg = smoke_cfg()
    cfg.use_model = False
    cfg.poll_s = 0.2
    engine = RecallEngine(cfg, get_title=lambda: FAKE_TITLES[0], get_idle=lambda: 0.0,
                          launch_hud=lambda *a: None)
    hk = GlobalHotkey(engine.toggle_pause)
    if not hk.start():
        code = hk.win32_error
        record(name, "FAIL", f"RegisterHotKey failed: Win32 error {code}"
               + (" (ERROR_HOTKEY_ALREADY_REGISTERED: another program uses Ctrl+Alt+P)"
                  if code == 1409 else "") + (f"; {hk.error}" if hk.error else ""))
        return
    engine.start()
    steps = []
    try:
        for want in (True, False):
            sent = press_ctrl_alt_p()
            t0 = time.monotonic()
            got = wait_until(lambda: engine.is_paused == want, 2.0, 0.01)
            steps.append(f"sent {sent}/6 events -> is_paused={engine.is_paused} "
                         f"after {time.monotonic() - t0:.2f}s")
            if not got:
                break
    finally:
        engine.stop()
        hk.stop()
    unregistered = not hk.registered and not hk._thread.is_alive()
    ok = len(steps) == 2 and engine.is_paused is False and "is_paused=True" in steps[0] and unregistered
    record(name, "PASS" if ok else "FAIL",
           "; ".join(steps) + f"; hotkey unregistered and thread ended={unregistered}")


# ------------------------------------------------------------ step 4
def step_engine(ollama_running: bool) -> None:
    name = "4 engine + real Ollama"
    if not ollama_running:
        record(name, "SKIP", "Ollama is not running")
        return
    from app.engine import RecallEngine, RecapReady, StateChanged, State

    cfg = smoke_cfg()
    cfg.backend = "ollama"
    cfg.away_s = 2
    cfg.poll_s = 0.2
    lock = threading.Lock()
    calls = {"n": 0}
    phase = {"away": False, "back": False}
    events, huds = [], []
    got_recap = threading.Event()

    def get_title():
        with lock:
            calls["n"] += 1
            return FAKE_TITLES[min(calls["n"] // 3, len(FAKE_TITLES) - 1)]

    started = time.monotonic()

    def get_idle():
        if phase["back"]:
            return 0.0
        if time.monotonic() - started > 3.0:          # titles logged; now "walk away"
            phase["away"] = True
            return 10.0 + (time.monotonic() - started)
        return 0.0

    def on_event(ev):
        events.append(ev)
        if isinstance(ev, RecapReady):
            got_recap.set()

    engine = RecallEngine(cfg, get_title=get_title, get_idle=get_idle,
                          launch_hud=lambda title, lines, s: huds.append((title, list(lines))),
                          on_event=on_event)
    engine.start()
    try:
        ok_recap = got_recap.wait(60)
        took = time.monotonic() - started
        phase["back"] = True                            # come back -> recap is "shown"
        wait_until(lambda: bool(huds), 5)
    finally:
        engine.stop()
    recaps = [e for e in events if isinstance(e, RecapReady)]
    changes = [f"{e.old.value}->{e.new.value}" for e in events if isinstance(e, StateChanged)]
    logged = [w.title for w in engine.snapshot().recent]
    if not ok_recap or not recaps:
        record(name, "FAIL", f"no recap_ready within 60 s; states: {changes}; logged: {logged}")
        return
    r = recaps[0]
    lines_ok = 1 <= len(r.lines) <= 3
    model_used = r.source in ("model", "mixed")
    detail = (f"recap_ready after {took:.1f}s, source={r.source}, {len(r.lines)} lines: "
              + " | ".join(r.lines)
              + f"; logged={logged}; states: {', '.join(changes)}; HUD launcher called={bool(huds)}")
    record(name, "PASS" if lines_ok and model_used and huds else "FAIL", detail)


def main() -> int:
    enable_dpi_awareness()
    print("RecallPlug live smoke test - windows may flash and a beep may play.\n", flush=True)
    try:
        ollama = step_selfcheck()
    except Exception as exc:
        record("1 self-check (real Ollama)", "FAIL", f"crashed: {type(exc).__name__}: {exc}")
        ollama = False
    for step, label in ((step_hud, "2 recap window"), (step_hotkey, "3 Ctrl+Alt+P hotkey"),
                        (lambda: step_engine(ollama), "4 engine + real Ollama")):
        try:
            step()
        except Exception as exc:
            record(label, "FAIL", f"crashed: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 78)
    print(f"{'step':<28} {'result':<7} details")
    print("-" * 78)
    for step, status, details in results:
        print(f"{step:<28} {status:<7} {details}")
    print("=" * 78)
    failed = [s for s, st, _ in results if st == "FAIL"]
    print("RESULT:", "FAIL (" + ", ".join(failed) + ")" if failed else "PASS (no FAIL)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
