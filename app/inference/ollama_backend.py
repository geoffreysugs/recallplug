"""Local model through the Ollama HTTP API (cfg.ollama_url)."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from ..config import Config
from .base import InferenceBackend, host_metrics, now_stamp, start_host_sample


class OllamaBackend(InferenceBackend):
    name = "ollama"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def health(self) -> bool:
        parts = urllib.parse.urlsplit(self.cfg.ollama_url)
        url = f"{parts.scheme}://{parts.netloc}/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def generate_text(self, prompt: str) -> tuple[str, dict]:
        cfg = self.cfg
        start_host_sample()
        body = json.dumps({
            "model": cfg.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": cfg.keep_alive,
            "options": {"temperature": 0.1, "num_predict": cfg.num_predict},
        }).encode("utf-8")
        req = urllib.request.Request(
            cfg.ollama_url, data=body, headers={"Content-Type": "application/json"}
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=cfg.request_timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        wall = time.perf_counter() - t0

        ns = 1e9
        load = data.get("load_duration", 0) / ns
        prompt_eval = data.get("prompt_eval_duration", 0) / ns
        eval_s = data.get("eval_duration", 0) / ns
        tokens = data.get("eval_count", 0)
        metrics = {
            "timestamp": now_stamp(),
            "model": cfg.model,
            "load_s": round(load, 3),
            "ttft_approx_s": round(load + prompt_eval, 3),
            "tokens": tokens,
            "tokens_per_s": round(tokens / eval_s, 2) if eval_s > 0 else 0.0,
            "wall_s": round(wall, 3),
            **host_metrics(),
            "label": "MEASURED (host, Ollama)",
        }
        return data.get("response", ""), metrics
