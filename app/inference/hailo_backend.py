"""ASUS UGen300 / Hailo-10H NPU backend - placeholder until Stage II."""
from __future__ import annotations

from ..config import Config
from .base import InferenceBackend


class HailoBackend(InferenceBackend):
    name = "hailo"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def health(self) -> bool:
        return False

    def generate_text(self, prompt: str) -> tuple[str, dict]:
        raise NotImplementedError("planned for Stage II")
