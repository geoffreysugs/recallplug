"""Semi-transparent welcome-back window in the bottom-right corner.

It runs as its own small process so it can never freeze the main program.
The recap reaches it as one UTF-8 JSON object on standard input (--stdin),
so no title or bullet text ever appears on the command line.
Test it alone with:   python -m app.hud --sample
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .win_api import enable_dpi_awareness, get_work_area

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WIDTH = 440
TASKBAR_MARGIN = 68    # fallback only: keeps the window above a bottom taskbar
WORK_AREA_MARGIN = 8   # gap to the work area's bottom-right corner
MIN_HEIGHT = 150
MAX_SCREEN_SHARE = 0.5  # the window never grows taller than half the screen
BULLET_FONT_SIZE = 11


def _usable(area, screen_w: int, screen_h: int) -> bool:
    """A work area we can trust: 4 numbers, non-empty and inside the screen."""
    try:
        left, top, right, bottom = (int(v) for v in area)
    except (TypeError, ValueError):
        return False
    return (0 <= left < right <= screen_w and 0 <= top < bottom <= screen_h
            and right - left >= WIDTH + 2 * WORK_AREA_MARGIN and bottom - top >= MIN_HEIGHT)


def hud_geometry(wanted_height: int, screen_w: int, screen_h: int,
                 work_area: tuple[int, int, int, int] | None = None) -> tuple[int, int, int, int]:
    """(width, height, x, y). Height follows the text between MIN_HEIGHT and
    half the screen; the bottom edge stays fixed, so a taller window grows
    UPWARD.

    With a usable work area (the screen without the taskbar, wherever the
    taskbar is) the window sits WORK_AREA_MARGIN px inside its bottom-right
    corner. Without one it falls back to the old fixed TASKBAR_MARGIN above
    the screen bottom."""
    height = min(int(screen_h * MAX_SCREEN_SHARE), max(MIN_HEIGHT, wanted_height))
    if work_area is not None and _usable(work_area, screen_w, screen_h):
        left, top, right, bottom = (int(v) for v in work_area)
        height = min(height, bottom - top - 2 * WORK_AREA_MARGIN)
        x = right - WIDTH - WORK_AREA_MARGIN
        y = bottom - height - WORK_AREA_MARGIN
        return WIDTH, height, x, y
    x = screen_w - WIDTH - 20
    y = screen_h - height - TASKBAR_MARGIN
    return WIDTH, height, x, y


def hud_command() -> list[str]:
    """The HUD's command line. It never carries any recap text."""
    return [sys.executable, "-m", "app.hud", "--stdin"]


def encode_payload(title: str, bullets: list[str], seconds: int) -> bytes:
    """{"title", "bullets", "seconds"} as one UTF-8 JSON object."""
    data = {"title": title, "bullets": list(bullets), "seconds": int(seconds)}
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def decode_payload(raw: bytes) -> tuple[str, list[str], int]:
    """The reverse of encode_payload; missing fields get safe defaults."""
    data = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    title = str(data.get("title") or "Welcome back")
    bullets = [str(b).strip() for b in data.get("bullets") or [] if str(b).strip()]
    return title, bullets, int(data.get("seconds") or 15)


def launch_hud(title: str, bullets: list[str], seconds: int = 15) -> None:
    """Start the HUD in a separate, windowless process and hand it the recap
    over standard input."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(hud_command(), cwd=str(PROJECT_ROOT), creationflags=flags,
                            stdin=subprocess.PIPE)
    try:
        proc.stdin.write(encode_payload(title, bullets, seconds))
        proc.stdin.close()
    except OSError:          # the HUD died before reading; the main program goes on
        pass


def show_hud(title: str, bullets: list[str], seconds: int = 15) -> None:
    import tkinter as tk

    enable_dpi_awareness()
    bg, fg, accent, muted = "#0f1b2d", "#ffffff", "#00b0f0", "#8fa3bf"

    root = tk.Tk()
    root.title("RecallPlug HUD")
    root.overrideredirect(True)              # no title bar
    root.attributes("-topmost", True)        # always on top
    root.attributes("-alpha", 0.94)          # 94% opaque
    root.configure(bg=bg)

    tk.Frame(root, bg=accent, width=5).pack(side="left", fill="y")
    body = tk.Frame(root, bg=bg, padx=16, pady=14)
    body.pack(side="left", fill="both", expand=True)

    wrap = WIDTH - 5 - 32
    tk.Label(body, text=title, bg=bg, fg=accent, font=("Segoe UI", 11, "bold"),
             anchor="w", justify="left", wraplength=wrap).pack(fill="x", pady=(0, 8))
    for b in bullets:
        tk.Label(body, text="\u2022 " + b, bg=bg, fg=fg, font=("Segoe UI", BULLET_FONT_SIZE),
                 anchor="w", justify="left", wraplength=wrap).pack(fill="x", pady=2)
    tk.Label(body, text="Click to dismiss", bg=bg, fg=muted, font=("Segoe UI", 8),
             anchor="w").pack(fill="x", pady=(8, 0))

    # Height follows the text (no scrollbar, no close button). The bottom edge
    # stays fixed just inside the work area (above or beside the taskbar), so
    # longer text grows the window UPWARD. DPI awareness is on (set before
    # tk.Tk()), so the screen size and the work area are both real pixels.
    root.update_idletasks()
    w, h, x, y = hud_geometry(root.winfo_reqheight(), root.winfo_screenwidth(),
                              root.winfo_screenheight(), get_work_area())
    root.geometry(f"{w}x{h}+{x}+{y}")

    def close(_event=None):
        root.destroy()

    for widget in [root, body] + list(body.winfo_children()):
        widget.bind("<Button-1>", close)
    root.after(max(1, seconds) * 1000, close)
    root.mainloop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stdin", action="store_true",
                    help='read {"title", "bullets", "seconds"} as UTF-8 JSON from standard input')
    ap.add_argument("--seconds", type=int, default=15)
    ap.add_argument("--sample", action="store_true", help="show sample text")
    ap.add_argument("--sample-long", action="store_true",
                    help="show three ~150-character fake bullets to check the height limit")
    args = ap.parse_args()
    if args.sample_long:
        title = "Welcome back - you were away 12 min"
        bullets = [
            "You stopped in Sample Quarterly Planning Workbook.xlsx (Excel), on the sheet that "
            "lists every regional supplier together with their delivery windows",
            "You were comparing freight quotes from several sample carriers against last "
            "quarter's averages to see which routes became more expensive this year",
            "Next: finish the comparison table in Sample Quarterly Planning Workbook.xlsx and "
            "mark the three routes that need a second quote before Friday's review",
        ]
    elif args.sample:
        title = "Welcome back - you were away 5 min"
        bullets = [
            "You were comparing quotes in Q3 Supplier Forecast.xlsx (Excel)",
            "Contract_Draft_v2.docx (Word) was open just before that",
            "Resume with the unit-price column in the Excel sheet",
        ]
    elif args.stdin:
        title, bullets, args.seconds = decode_payload(sys.stdin.buffer.read())
    else:
        ap.error("use --stdin (as launch_hud does), --sample or --sample-long")
    show_hud(title, bullets, args.seconds)


if __name__ == "__main__":
    main()
