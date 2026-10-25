"""The interface every inference backend implements, plus host-usage helpers."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

try:                       # optional: pip install psutil
    import psutil
except Exception:          # pragma: no cover
    psutil = None


class InferenceBackend(ABC):
    name = "base"

    @abstractmethod
    def generate_text(self, prompt: str) -> tuple[str, dict]:
        """Returns (text, metrics). Raises on any failure."""

    @abstractmethod
    def health(self) -> bool:
        """True if the backend can answer right now."""


def start_host_sample() -> None:
    """Call right before the work so host_metrics() measures CPU over it."""
    if psutil:
        psutil.cpu_percent(None)


def host_metrics() -> dict:
    return {
        "host_cpu_pct": psutil.cpu_percent(None) if psutil else "",
        "host_ram_pct": psutil.virtual_memory().percent if psutil else "",
    }


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
