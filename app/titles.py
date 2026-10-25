"""Window-title cleaning, privacy filtering and the in-memory activity log."""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass

from .config import Config
from .redact import redact

# lower-case fragment found at the END of a title  ->  clean app name
KNOWN_APPS = {
    "google chrome": "Chrome",
    "microsoft edge": "Edge",
    "mozilla firefox": "Firefox",
    "visual studio code": "VS Code",
    "excel": "Excel",
    "word": "Word",
    "powerpoint": "PowerPoint",
    "outlook": "Outlook",
    "notepad": "Notepad",
    "adobe acrobat": "Acrobat",
    "onenote": "OneNote",
    "slack": "Slack",
    "microsoft teams": "Teams",
    "記事本": "Notepad",             # 記事本
    "檔案總管": "File Explorer", # 檔案總管
}

_SPLIT = re.compile(r"\s+[-\u2013\u2014]\s+")
_PROFILE_NAMES = {"personal", "work", "school"}   # Edge profile labels


def clean_title(raw: str) -> tuple[str, str]:
    """'Q3 Forecast.xlsx - Excel'  ->  ('Q3 Forecast.xlsx', 'Excel')."""
    text = raw.replace("\u200b", "").strip()
    parts = _SPLIT.split(text)
    app = ""
    if len(parts) > 1:
        last = parts[-1].strip().lower()
        for fragment, name in KNOWN_APPS.items():
            if fragment in last:
                app = name
                parts = parts[:-1]
                break
        if app and parts and parts[-1].strip().lower() in _PROFILE_NAMES and len(parts) > 1:
            parts = parts[:-1]
    title = " - ".join(p.strip() for p in parts if p.strip()) or text
    return title, app


def is_blocked(raw: str, cfg: Config) -> bool:
    """True if this window must NOT be logged (empty, ours, or private)."""
    low = raw.strip().lower()
    if len(low) < 3:
        return True
    exact = {item.lower() for item in cfg.ignore_exact}
    if low in exact or clean_title(raw)[0].strip().lower() in exact:   # 'New Tab - Google Chrome'
        return True
    if any(item in low for item in cfg.ignore_titles):
        return True
    if any(word in low for word in cfg.blacklist_keywords):
        return True
    return False


@dataclass
class Entry:
    ts: float
    title: str
    app: str


class ActivityLog:
    """Remembers the most recent distinct windows. Memory only.
    Every title is redacted (emails, phones, cards, IDs) before it is stored."""

    def __init__(self, max_entries: int = 25) -> None:
        self._items: deque[Entry] = deque(maxlen=max_entries)

    def add(self, title: str, app: str, ts: float | None = None) -> bool:
        """Add an entry unless it is the same as the previous one."""
        title = redact(title)
        if self._items and self._items[-1].title == title:
            return False
        self._items.append(Entry(ts if ts is not None else time.time(), title, app))
        return True

    def recent(self, n: int) -> list[Entry]:
        """Last n entries, oldest first."""
        return list(self._items)[-n:]

    def __len__(self) -> int:
        return len(self._items)
