"""Start-up self-check: is Ollama running, and is the configured model installed?

    result = check(cfg)              # SelfCheckResult, never raises
    pulled = ensure_model(cfg, cb)   # PullResult; Ollama downloads, we only talk to it

The program itself only talks to the local Ollama on 127.0.0.1 (the host in
cfg.ollama_url must be a loopback address). Ollama does the download.
Results are plain objects so a GUI can use them as well as the command line.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from .config import Config

# ollama_state
NOT_RUNNING = "NOT_RUNNING"
RUNNING = "RUNNING"
# model_state
MISSING = "MISSING"
PRESENT = "PRESENT"
UNKNOWN = "UNKNOWN"

INSTALL_URL = "https://ollama.com"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MODEL_SIZES = {"qwen2.5:1.5b": "about 1 GB"}       # rough download sizes for the prompt


@dataclass(frozen=True)
class SelfCheckResult:
    ollama_state: str        # NOT_RUNNING | RUNNING
    model_state: str         # MISSING | PRESENT | UNKNOWN
    model_name: str
    hint: str                # short English text for the user

    @property
    def ok(self) -> bool:
        return self.ollama_state == RUNNING and self.model_state == PRESENT


@dataclass(frozen=True)
class PullResult:
    ok: bool
    message: str


def base_url(cfg: Config) -> str:
    """'http://127.0.0.1:11434' from cfg.ollama_url. Raises ValueError for a
    host that is not this computer, so nothing is ever sent elsewhere."""
    parts = urllib.parse.urlsplit(cfg.ollama_url)
    if (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise ValueError("the Ollama address must be on this computer (127.0.0.1)")
    return f"{parts.scheme}://{parts.netloc}"


def model_size(name: str) -> str:
    return MODEL_SIZES.get(name, "size unknown")


def _installed(names: list[str], model: str) -> bool:
    wanted = model if ":" in model else model + ":latest"
    return model in names or wanted in names


def check(cfg: Config, timeout: float = 2.0) -> SelfCheckResult:
    """GET /api/tags on the local Ollama and look for cfg.model."""
    model = cfg.model
    try:
        url = base_url(cfg) + "/api/tags"
    except ValueError as exc:
        return SelfCheckResult(NOT_RUNNING, UNKNOWN, model, str(exc).capitalize() + ".")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        return SelfCheckResult(
            NOT_RUNNING, UNKNOWN, model,
            f"Ollama is not running. Install it from {INSTALL_URL}, then start the Ollama app "
            "(or run 'ollama serve').")
    try:
        data = json.loads(raw.decode("utf-8"))
        names = [str(m.get("name") or m.get("model") or "") for m in data.get("models") or []]
    except Exception:
        return SelfCheckResult(RUNNING, UNKNOWN, model,
                               "Ollama answered, but its model list could not be read.")
    if _installed(names, model):
        return SelfCheckResult(RUNNING, PRESENT, model, f"Ollama is running and {model} is installed.")
    return SelfCheckResult(RUNNING, MISSING, model,
                           f"The model {model} is not installed ({model_size(model)}). "
                           f"Download it with: ollama pull {model}")


ProgressCallback = Callable[[Optional[float], str], None]


def ensure_model(cfg: Config, progress_callback: Optional[ProgressCallback] = None,
                 timeout: float = 30.0) -> PullResult:
    """Ask the local Ollama to download cfg.model (POST /api/pull, streamed).

    progress_callback(percent, status) is called for each progress line;
    percent is 0-100 for download lines and None for other steps.
    Ctrl+C ends the wait and returns a failed PullResult (Ollama may finish
    or drop the download on its own). Never raises for network problems."""
    model = cfg.model
    try:
        url = base_url(cfg) + "/api/pull"
    except ValueError as exc:
        return PullResult(False, str(exc).capitalize() + ".")
    body = json.dumps({"model": model, "stream": True}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})

    def report(percent, status):
        if progress_callback:
            try:
                progress_callback(percent, status)
            except Exception:
                pass

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if item.get("error"):
                    return PullResult(False, f"Download failed: {str(item['error'])[:200]}")
                status = str(item.get("status") or "")
                total, done = item.get("total"), item.get("completed")
                if total and done is not None:
                    report(min(100.0, max(0.0, 100.0 * float(done) / float(total))), status)
                else:
                    report(None, status)
                if status == "success":
                    return PullResult(True, f"{model} is installed.")
    except KeyboardInterrupt:
        return PullResult(False, "Download cancelled.")
    except urllib.error.HTTPError as exc:
        return PullResult(False, f"Download failed: Ollama answered HTTP {exc.code}.")
    except Exception as exc:
        return PullResult(False, f"Download failed: could not reach Ollama ({type(exc).__name__}).")
    return PullResult(False, "Download failed: Ollama stopped before the model was complete.")


# ------------------------------------------------------------ command line
def needs_check(cfg: Config) -> bool:
    """The CLI skips the check with --backend mock or --no-model."""
    return cfg.use_model and cfg.backend != "mock"


def run_cli_check(cfg: Config, assume_yes: bool = False,
                  ask: Callable[[str], str] = input,
                  out: Callable[[str], None] = print) -> int:
    """Command-line self-check. Returns 0 to continue, or a non-zero exit code."""
    result = check(cfg)
    if result.ollama_state == NOT_RUNNING:
        out("Ollama is not running, so the local model cannot be used.")
        out(f"  Install Ollama from {INSTALL_URL} (free), then start the Ollama app")
        out("  or run 'ollama serve' in another window, and start RecallPlug again.")
        out("  To try RecallPlug without a model: python -m app.main --no-model")
        return 2
    if result.model_state == PRESENT:
        return 0
    if result.model_state == UNKNOWN:
        out(f"warning: {result.hint} Continuing.")
        return 0
    out(f"The model {cfg.model} is not installed yet ({model_size(cfg.model)} download).")
    if assume_yes:
        out("Download it now with Ollama? [y/N] y (--yes)")
        answer = "y"
    else:
        try:
            answer = ask("Download it now with Ollama? [y/N] ")
        except EOFError:
            answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        out(f"Not downloaded. You can do it later with: ollama pull {cfg.model}")
        return 3

    last = {"shown": -10.0, "status": ""}

    def progress(percent, status):
        if percent is not None and percent < last["shown"]:
            last["shown"] = -10.0             # the next layer starts again at 0 %
        if percent is None:
            if status and status != last["status"]:
                out(f"  {status}")
                last["status"] = status
        elif percent >= last["shown"] + 10 or percent >= 100:
            out(f"  {percent:5.1f}%")
            last["shown"] = percent

    pulled = ensure_model(cfg, progress)
    out(pulled.message)
    return 0 if pulled.ok else 4
