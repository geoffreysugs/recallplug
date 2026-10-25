"""Picks the backend named in cfg.backend; unhealthy choices fall back to Ollama."""
from __future__ import annotations

from ..config import Config
from .base import InferenceBackend
from .hailo_backend import HailoBackend
from .mock_backend import MockBackend
from .ollama_backend import OllamaBackend

BACKENDS = {"ollama": OllamaBackend, "hailo": HailoBackend, "mock": MockBackend}


def get_backend(cfg: Config) -> InferenceBackend:
    cls = BACKENDS.get(cfg.backend)
    if cls is None:
        print(f"[backend] unknown backend '{cfg.backend}' - using ollama")
        return OllamaBackend(cfg)
    backend = cls(cfg)
    if cls is not OllamaBackend and not backend.health():
        print(f"[backend] {backend.name} is not available - using ollama")
        return OllamaBackend(cfg)
    return backend
