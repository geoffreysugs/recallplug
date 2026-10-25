"""Semi-transparent welcome-back window in the bottom-right corner.

It runs as its own small process so it can never freeze the main program.
Test it alone with:   python -m app.hud --sample
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .win_api import enable_dpi_awareness

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEP = " ||| "          # separates bullets on the command line
WIDTH = 440
TASKBAR_MARGIN = 68    # keeps the window above the Windows taskbar
MIN_HEIGHT = 150
MAX_SCREEN_SHARE = 0.5  # the window never grows taller than half the screen
BULLET_FONT_SIZE = 11


def hud_geometry(wanted_height: int, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """(width, height, x, y). Height follows the text between MIN_HEIGHT and
    half the screen; the bottom edge stays fixed above the taskbar, so a
    taller window grows UPWARD."""
    height = min(int(screen_h * MAX_SCREEN_SHARE), max(MIN_HEIGHT, wanted_height))
    x = screen_w - WIDTH - 20
    y = screen_h - height - TASKBAR_MARGIN
    return WIDTH, height, x, y


def launch_hud(title: str, bullets: list[str], seconds: int = 15) -> None:
    """Start the HUD in a separate, windowless process."""
    cmd = [sys.executable, "-m", "app.hud",
           "--title", title, "--bullets", SEP.join(bullets),
           "--seconds", str(seconds)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), creationflags=flags)


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
    # stays fixed above the taskbar, so longer text grows the window UPWARD.
    # DPI awareness is on, so winfo_screenheight() is the real screen height.
    root.update_idletasks()
    w, h, x, y = hud_geometry(root.winfo_reqheight(), root.winfo_screenwidth(),
                              root.winfo_screenheight())
    root.geometry(f"{w}x{h}+{x}+{y}")

    def close(_event=None):
        root.destroy()

    for widget in [root, body] + list(body.winfo_children()):
        widget.bind("<Button-1>", close)
    root.after(max(1, seconds) * 1000, close)
    root.mainloop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="Welcome back")
    ap.add_argument("--bullets", default="")
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
    else:
        title = args.title
        bullets = [b.strip() for b in args.bullets.split("|||") if b.strip()]
    show_hud(title, bullets, args.seconds)


if __name__ == "__main__":
    main()
