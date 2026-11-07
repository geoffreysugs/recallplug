"""User-triggered export of the recent windows and the latest recap.

This is the ONLY place where window titles or a recap are written to disk, and
only after the user confirms WARNING. Files go to exports/ (git-ignored):
    exports/recallplug_YYYYMMDD_HHMMSS.txt
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIR = PROJECT_ROOT / "exports"
WARNING = "This file will contain your recent window titles and the latest recap, unencrypted."


def export_text(snapshot, now: float) -> str:
    lines = [f"Recall Plug export - {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}",
             "", "Recent windows (oldest first):"]
    if snapshot.recent:
        for e in snapshot.recent:
            label = f"{e.title} ({e.app})" if e.app else e.title
            lines.append(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e.ts))}  {label}")
    else:
        lines.append("(none)")
    lines += ["", "Latest recap:"]
    lines += [f"- {line}" for line in snapshot.latest_recap] if snapshot.latest_recap else ["(none)"]
    return "\n".join(lines) + "\n"


def export_path(folder: Path, now: float) -> Path:
    stem = "recallplug_" + time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
    path = folder / f"{stem}.txt"
    n = 2
    while path.exists():
        path = folder / f"{stem}_{n}.txt"
        n += 1
    return path


def export_snapshot(snapshot, folder: Optional[Path] = None, now: Optional[float] = None) -> Path:
    """Write the export file and return its path. Raises OSError on failure."""
    now = time.time() if now is None else now
    folder = Path(folder) if folder is not None else EXPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = export_path(folder, now)
    with open(path, "x", encoding="utf-8", newline="\r\n") as f:
        f.write(export_text(snapshot, now))
    return path
