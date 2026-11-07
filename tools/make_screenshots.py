"""Scripted fake-data session of the Recall Plug window, plus screenshots.

    python -B -m tools.make_screenshots              # run the session, print what happened
    python -B -m tools.make_screenshots --png docs/gui.png
    <venv>\\Scripts\\python -B -m tools.make_screenshots --gif docs/demo.gif   # needs Pillow

The window gets FAKE window titles, a fake idle timer and the mock backend
(or --ollama for the real local model). Images come from PrintWindow on the
app's own window handle, never from a screen grab, so nothing else on the
desktop is captured. PNG files are written with the standard library; the
GIF needs Pillow (dev-only, see tools/README.md).
"""
from __future__ import annotations

import argparse
import ctypes
import struct
import sys
import time
import zlib
from pathlib import Path

from app.config import Config
from app.engine import RecallEngine, State
from app.inference.mock_backend import MockBackend

FAKE_TITLES = [
    "Supplier Quotes.txt - Notepad",
    "Q3 Forecast.xlsx - Excel",
    "Meeting Notes.txt - Notepad",
    "Contract Draft v2.txt - Notepad",
]
PRIVATE_TITLE = "Private Diary.txt - Notepad"     # shown only while paused: must not be logged
SPEED = 60.0                                       # fake clock: 1 real second = 1 minute


# ------------------------------------------------------------ capture
def capture_window(hwnd: int) -> tuple[int, int, bytes]:
    """PrintWindow(PW_RENDERFULLCONTENT) of one window -> (width, height, BGRA rows),
    cropped to the visible frame (without the invisible resize borders)."""
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    frame = wintypes.RECT()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame))
    except Exception:
        frame = rect
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, 2):
            raise OSError("PrintWindow failed")

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        data = buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)
    # crop to the DWM frame
    x0, y0 = max(0, frame.left - rect.left), max(0, frame.top - rect.top)
    x1, y1 = min(w, frame.right - rect.left), min(h, frame.bottom - rect.top)
    if x1 - x0 < 10 or y1 - y0 < 10:
        return w, h, data
    rows = [data[(y * w + x0) * 4:(y * w + x1) * 4] for y in range(y0, y1)]
    return x1 - x0, y1 - y0, b"".join(rows)


def bgra_to_rgb(w: int, h: int, bgra: bytes) -> bytes:
    out = bytearray(w * h * 3)
    out[0::3], out[1::3], out[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return bytes(out)


def write_png(path: Path, w: int, h: int, bgra: bytes) -> None:
    """Standard-library PNG writer (8-bit RGB)."""
    rgb = bgra_to_rgb(w, h, bgra)
    raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


# ------------------------------------------------------------ the session
class FakeDesk:
    def __init__(self):
        self.t0 = time.monotonic()
        self.base = time.time() - 12 * 60          # the session "started" 12 minutes ago
        self.title = FAKE_TITLES[0]
        self.idle = 0.0

    def clock(self) -> float:
        return self.base + (time.monotonic() - self.t0) * SPEED

    def get_title(self) -> str:
        return self.title

    def get_idle(self) -> float:
        return self.idle


def run_session(png=None, gif=None, use_ollama=False, ask_question=None) -> dict:
    import tkinter as tk
    from app import selfcheck
    from app.gui import RecallPlugApp
    from app.win_api import enable_dpi_awareness

    enable_dpi_awareness()
    cfg = Config()
    cfg.metrics_csv = ""                       # write no files
    cfg.away_s = 2 * 60                        # fake clock minutes; idle below is in "minutes"
    cfg.poll_s = 0.1
    cfg.backend = "ollama" if use_ollama else "mock"
    desk = FakeDesk()
    huds, observed, frames = [], {"states": [], "notes": []}, []
    backend = None if use_ollama else MockBackend(cfg)

    def factory(on_event):
        return RecallEngine(cfg, get_title=desk.get_title, get_idle=desk.get_idle,
                            clock=desk.clock, backend=backend,
                            launch_hud=lambda *a: huds.append(a), on_event=on_event)

    root = tk.Tk()
    app = RecallPlugApp(root, cfg, skip_check=not use_ollama,
                        skip_reason="" if use_ollama else "mock backend",
                        engine_factory=factory, hotkey_factory=None, clock=desk.clock,
                        poll_ms=200, drain_ms=50)
    hwnd = int(root.wm_frame(), 16)
    app.start()

    def note(text):
        observed["notes"].append(f"{time.monotonic() - desk.t0:4.1f}s {text}")
        print("  " + observed["notes"][-1], flush=True)

    def state():
        return app.status_labels["engine"].cget("text")

    def frame(hold=1):
        if gif:
            root.update()
            frames.append((capture_window(hwnd), hold))

    steps = []

    def at(seconds, fn):
        steps.append((seconds, fn))

    for i, title in enumerate(FAKE_TITLES):
        at(0.4 + i * 0.7, lambda t=title: setattr(desk, "title", t))
        at(0.6 + i * 0.7, lambda: frame())
    at(3.4, lambda: note(f"recent list ({app.recent_list.size()}): "
                         + " | ".join(app.recent_list.get(0, "end"))))
    at(3.5, lambda: (app.on_pause(), note(f"pressed Pause -> state '{state()}', button "
                                          f"'{app.buttons['pause'].cget('text')}'")))
    at(3.7, lambda: setattr(desk, "title", PRIVATE_TITLE))
    at(4.3, lambda: (frame(2), note(f"while paused: list size {app.recent_list.size()}, "
                                    f"private title logged="
                                    f"{any('Private' in x for x in app.recent_list.get(0, 'end'))}")))
    at(4.5, lambda: (setattr(desk, "title", FAKE_TITLES[-1]), app.on_pause(),
                     note(f"pressed Resume -> state '{state()}'")))
    at(5.0, lambda: setattr(desk, "idle", 30 * 60.0))          # walk away (fake minutes)
    at(5.6, lambda: (note(f"idle -> state '{state()}'"), frame()))

    if ask_question and hasattr(app, "ask_entry"):
        def type_question():
            app.ask_entry.configure(state="normal")
            app.ask_entry.delete(0, "end")
            app.ask_entry.insert(0, ask_question)
            app.render()
            frame()

        def press_ask():
            note(f"asked: {ask_question!r}")
            app.on_ask()
        at(8.5, type_question)
        at(9.0, press_ask)

    deadline = 60.0 if use_ollama else 25.0
    t_back = {"done": False}

    def tick():
        elapsed = time.monotonic() - desk.t0
        while steps and steps[0][0] <= elapsed:
            _, fn = steps.pop(0)
            fn()
        s = state()
        if not observed["states"] or observed["states"][-1] != s:
            observed["states"].append(s)
        recap = app.recap_label.cget("text")
        if desk.idle and "You stopped in" in recap and not t_back["done"]:
            t_back["done"] = True
            note("recap shown: " + recap.replace("\n\n", " | "))
            frame(2)
            desk.idle = 0.0                                     # come back
        answered = (not ask_question or not hasattr(app, "answer_text")
                    or app.presenter.ask_source is not None)
        if t_back["done"] and not steps and answered and elapsed > 7:
            if ask_question and hasattr(app, "answer_text"):
                note(f"answer ({app.presenter.ask_source}): {app.presenter.ask_answer!r}")
            note(f"back -> state '{s}', HUD launcher called {len(huds)}x")
            root.after(800, finish)
            return
        if elapsed > deadline:
            note("TIMEOUT")
            finish()
            return
        root.after(50, tick)

    def finish():
        root.update()
        frame(4)
        if png:
            w, h, data = capture_window(hwnd)
            write_png(Path(png), w, h, data)
            note(f"saved {png} ({w}x{h}) via PrintWindow")
        observed["recent"] = list(app.recent_list.get(0, "end"))
        observed["recap"] = app.recap_label.cget("text")
        observed["huds"] = len(huds)
        engine = app.engine
        app.close()
        observed["engine_stopped"] = engine is not None and not engine.is_running

    root.after(50, tick)
    root.mainloop()
    if gif:
        save_gif(Path(gif), frames)
    observed["frames"] = len(frames)
    return observed


def save_gif(path: Path, frames) -> None:
    from PIL import Image                          # dev-only dependency
    images, durations = [], []
    for (w, h, data), hold in frames:
        img = Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA", 0, 1).convert("RGB")
        img = img.resize((w * 2 // 3, h * 2 // 3), Image.LANCZOS)
        images.append(img.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
        durations.append(900 * hold)
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:], duration=durations,
                   loop=0, optimize=True)
    print(f"  saved {path} ({len(images)} frames, {path.stat().st_size // 1024} KB)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--png", help="save a PrintWindow screenshot of the window at the end")
    ap.add_argument("--gif", help="save an animated GIF of the session (needs Pillow)")
    ap.add_argument("--ollama", action="store_true", help="use the real local Ollama")
    ap.add_argument("--ask", help="type this question into the ask box during the session")
    args = ap.parse_args(argv)
    if sys.platform != "win32":
        print("needs Windows")
        return 2
    print("Recall Plug fake-data session (fake titles, fake idle timer):", flush=True)
    obs = run_session(args.png, args.gif, args.ollama, args.ask)
    print("states seen:", " -> ".join(obs["states"]))
    print("engine stopped after close:", obs.get("engine_stopped"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
