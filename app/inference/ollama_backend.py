"""Local model through the Ollama HTTP API (cfg.ollama_url)."""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

from ..config import Config
from . import base
from .base import InferenceBackend, host_metrics, now_stamp, start_host_sample

PEAK_STEP_S = 0.1            # how often the peak sampler looks at the CPU


def busy_pct(before, after) -> float:
    """System-wide busy CPU percent between two psutil.cpu_times() readings
    (the same formula psutil.cpu_percent() uses)."""
    total = sum(after) - sum(before)
    if total <= 0:
        return 0.0
    idle = (after.idle - before.idle) + (getattr(after, "iowait", 0) - getattr(before, "iowait", 0))
    return round(max(0.0, min(100.0, 100.0 * (1 - idle / total))), 1)


class CpuPeak:
    """Peak system-wide CPU percent during one call, from cpu_times() deltas
    every PEAK_STEP_S. It never calls psutil.cpu_percent(), so the average
    from host_metrics() is not disturbed. peak() is "" without psutil."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._values: list[float] = []
        self._thread: threading.Thread | None = None

    def start(self) -> "CpuPeak":
        if base.psutil:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def _run(self) -> None:
        last, last_t = base.psutil.cpu_times(), time.perf_counter()
        while not self._stop.wait(PEAK_STEP_S):
            now, now_t = base.psutil.cpu_times(), time.perf_counter()
            self._values.append(busy_pct(last, now))
            last, last_t = now, now_t
        # the last stretch counts if it is long enough to be meaningful (or the only one)
        if not self._values or time.perf_counter() - last_t >= PEAK_STEP_S / 2:
            self._values.append(busy_pct(last, base.psutil.cpu_times()))

    def peak(self) -> float | str:
        if not self._thread:
            return ""
        self._stop.set()
        self._thread.join()
        return max(self._values)


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
        options = {"temperature": 0.1, "num_predict": cfg.num_predict}
        if cfg.cpu_only:
            options["num_gpu"] = 0           # no layers on the GPU
        body = json.dumps({
            "model": cfg.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": cfg.keep_alive,
            "options": options,
        }).encode("utf-8")
        req = urllib.request.Request(
            cfg.ollama_url, data=body, headers={"Content-Type": "application/json"}
        )
        start_host_sample()
        sampler = CpuPeak().start()
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=cfg.request_timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        finally:
            wall = time.perf_counter() - t0
            cpu_peak = sampler.peak()

        ns = 1e9
        load = data.get("load_duration", 0) / ns
        prompt_eval = data.get("prompt_eval_duration", 0) / ns
        eval_s = data.get("eval_duration", 0) / ns
        tokens = data.get("eval_count", 0)
        host = host_metrics()
        metrics = {
            "timestamp": now_stamp(),
            "model": cfg.model,
            "load_s": round(load, 3),
            "ttft_approx_s": round(load + prompt_eval, 3),
            "tokens": tokens,
            "tokens_per_s": round(tokens / eval_s, 2) if eval_s > 0 else 0.0,
            "wall_s": round(wall, 3),
            "host_cpu_pct": host["host_cpu_pct"],          # average over the call
            "host_cpu_peak_pct": cpu_peak,
            "host_ram_pct": host["host_ram_pct"],
            "label": "MEASURED (host, Ollama, CPU only)" if cfg.cpu_only else "MEASURED (host, Ollama)",
        }
        return data.get("response", ""), metrics
