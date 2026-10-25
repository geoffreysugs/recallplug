"""RecallPlug settings.

Edit the values here. Some can be overridden from the command line
(see app/main.py).  Nothing in this file talks to the network.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Config:
    # ---- Local inference (Ollama today; UGen300 backend planned for Stage II)
    backend: str = "ollama"          # "ollama" | "hailo" | "mock"  (see app/inference/)
    # 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first (~2 s delay)
    ollama_url: str = "http://127.0.0.1:11434/api/generate"
    model: str = "qwen2.5:1.5b"
    keep_alive: str = "24h"          # keep the model loaded between requests
    num_predict: int = 80            # max tokens in the answer (bullets 2 and 3)
    request_timeout_s: int = 90
    use_model: bool = True           # False (`--no-model`) = rule-only bullets, no model call

    # ---- Bullet 3 ("Next: ...") is rejected if it contains one of these whole
    #      words (case-insensitive); an empty tuple turns the check off
    risky_next_words: tuple = ("delete", "uninstall", "send", "pay", "purchase", "submit")
    # Bullet 3 must name the main words of one of the most recent N windows
    # (2 = the old strict rule: the last window or the one before it)
    next_ground_windows: int = 6

    # ---- Presence timers (real-world values; `--demo` shortens them)
    poll_s: float = 1.0              # how often we look at the screen state
    away_s: int = 300                # idle this long (5 min) = away
    welcome_back_idle_s: float = 2.0 # idle below this after being away = back

    # ---- Activity log (window titles only, kept in memory, never saved)
    max_log_entries: int = 25
    prompt_entries: int = 12         # how many recent windows go into the prompt

    # ---- Floating HUD
    hud_seconds: int = 15
    max_bullets: int = 3
    max_bullet_chars: int = 92
    max_title_chars: int = 48

    # ---- Privacy: windows whose title contains any of these are never logged
    blacklist_keywords: tuple = (
        "bank", "password", "incognito", "inprivate", "1password", "bitwarden",
        "keepass", "lastpass", "login", "sign in", "credit card",
        "\u7db2\u9280",   # online banking (Chinese)
        "\u5bc6\u78bc",   # password (Chinese)
        "\u7121\u75d5",   # incognito (Chinese)
    )

    # ---- Windows that are never useful to log (ours + system noise)
    ignore_titles: tuple = (
        "task switching", "lockapp", "program manager",
        "windows default lock screen", "windows powershell", "command prompt",
    )

    # ---- System windows ignored only on an EXACT title match (case-insensitive,
    #      with or without the app suffix such as " - Google Chrome"), so normal
    #      pages that merely contain these words are still logged
    ignore_exact: tuple = (
        "recallplug hud",          # our own HUD window (not pages that mention the project)
        "new tab",
        "新分頁",   # 新分頁 (New Tab, Chinese)
        "工作切換",   # 工作切換 (Alt+Tab, Chinese)
        "task switching",
        "工作檢視",   # 工作檢視 (Task View, Chinese)
        "task view",
    )

    # ---- Where timing numbers are saved (contains NO window titles; "" = off)
    metrics_csv: str = "bench/metrics.csv"
