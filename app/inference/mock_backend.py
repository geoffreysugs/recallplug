"""Canned answer, for demos and tests without any model.

It builds bullets 2 and 3 from the anchor windows in the prompt, so its
answer passes the same checks a good model answer would. A condense prompt
gets the sentence back, shortened at a word.
"""
from __future__ import annotations

import re
import time

from ..config import Config
from .base import InferenceBackend, host_metrics, now_stamp, start_host_sample

_ANCHOR = re.compile(r"^(Last window|Window before that): (.+)$", re.M)
_SENTENCE = re.compile(r"^Sentence: (.+)$", re.M)
_TITLE_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")


class MockBackend(InferenceBackend):
    name = "mock"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def health(self) -> bool:
        return True

    def _answer(self, prompt: str) -> str:
        sentence = _SENTENCE.search(prompt)
        if prompt.startswith("Rewrite") and sentence:
            text = sentence.group(1)
            return text if len(text) <= 78 else text[:78].rsplit(" ", 1)[0]
        found = dict(_ANCHOR.findall(prompt.split("Now the real data:", 1)[-1]))
        last = found.get("Last window", "your last document")
        topic = _TITLE_WORD.search(last)
        return (f"Work: You were working on {topic.group(0) if topic else 'your'} tasks.\n"
                f"Next: pick up where you left off in {last}")

    def generate_text(self, prompt: str) -> tuple[str, dict]:
        start_host_sample()
        t0 = time.perf_counter()
        text = self._answer(prompt)
        metrics = {
            "timestamp": now_stamp(),
            "model": "mock",
            "load_s": 0.0,
            "ttft_approx_s": 0.0,
            "tokens": len(text.split()),
            "tokens_per_s": 0.0,
            "wall_s": round(time.perf_counter() - t0, 3),
            **host_metrics(),
            "label": "MEASURED (host, mock backend)",
        }
        return text, metrics
