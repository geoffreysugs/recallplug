"""Pluggable local-inference backends (Ollama today, Hailo-10H planned)."""
from .base import InferenceBackend
from .factory import get_backend

__all__ = ["InferenceBackend", "get_backend"]
