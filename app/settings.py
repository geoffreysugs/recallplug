"""Settings saved by the Settings dialog: config/settings.json (git-ignored).

Order (later wins): built-in defaults < config/settings.json < command-line flags.
Changes apply at the next start. The file holds only these three values.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"
DEMO_AWAY_S = 10
MIN_AWAY, MAX_AWAY = 1, 60
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")


@dataclass(frozen=True)
class Settings:
    away_minutes: int = 5
    demo: bool = False
    model: str = Config.model


def validate(data: dict) -> tuple[Settings, list[str]]:
    """Returns (settings, problems). A bad value keeps its default and adds a problem."""
    d = Settings()
    problems: list[str] = []
    away = data.get("away_minutes", d.away_minutes)
    try:
        if isinstance(away, bool) or float(away) != int(float(away)):
            raise ValueError
        away = int(float(away))
        if not MIN_AWAY <= away <= MAX_AWAY:
            raise ValueError
    except (TypeError, ValueError):
        problems.append(f"Away time must be a whole number of minutes from {MIN_AWAY} to {MAX_AWAY}.")
        away = d.away_minutes
    demo = data.get("demo", d.demo)
    if not isinstance(demo, bool):
        problems.append("Demo mode must be on or off.")
        demo = d.demo
    model = data.get("model", d.model)
    if not isinstance(model, str) or not _MODEL_NAME.match(model.strip()):
        problems.append("Model name must look like 'qwen2.5:1.5b' (letters, digits, . : / - _).")
        model = d.model
    return Settings(away, demo, model.strip()), problems


def load(path: Optional[Path] = None) -> tuple[Settings, Optional[str]]:
    """Never raises: a missing file gives the defaults, a broken one the defaults
    plus a one-line warning."""
    path = Path(path) if path is not None else SETTINGS_PATH
    if not path.exists():
        return Settings(), None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        return Settings(), f"could not read {path.name} ({type(exc).__name__}); using defaults"
    settings, problems = validate(data)
    return settings, ("; ".join(problems) if problems else None)


def save(settings: Settings, path: Optional[Path] = None) -> Path:
    """Validate and write the file (created at first save). Raises ValueError for bad values."""
    checked, problems = validate(asdict(settings))
    if problems:
        raise ValueError(" ".join(problems))
    path = Path(path) if path is not None else SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(checked), indent=2) + "\n", encoding="utf-8")
    return path


def apply(cfg: Config, settings: Settings) -> Config:
    cfg.away_s = DEMO_AWAY_S if settings.demo else settings.away_minutes * 60
    cfg.model = settings.model
    return cfg
