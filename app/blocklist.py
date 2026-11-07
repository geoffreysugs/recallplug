"""Editable privacy block list (config/blocklist.txt).

A window whose title contains any keyword (case-insensitive substring) is
never logged. The file holds one keyword per line; lines starting with # are
comments and blank lines are ignored. If the file is missing it is created
from the built-in defaults (Config.blacklist_keywords); if it cannot be read,
the defaults are used and one warning is returned.

Path, in order: --blocklist PATH, the RECALLPLUG_BLOCKLIST environment
variable, then config/blocklist.txt in the project folder.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "config" / "blocklist.txt"
ENV_VAR = "RECALLPLUG_BLOCKLIST"

HEADER = """\
# RecallPlug block list
#
# A window whose title contains any word or phrase below is never logged.
# - one keyword per line (a phrase with spaces is fine, e.g. "sign in")
# - matching ignores upper/lower case and finds the keyword anywhere in the title
# - lines starting with # are comments; blank lines are ignored
# Save the file, then restart RecallPlug (a GUI can reload it without a restart).
# Delete this file to get the built-in list back.
"""


@dataclass(frozen=True)
class LoadResult:
    keywords: tuple          # lower-case keywords
    path: Path
    source: str              # 'file' | 'created' | 'defaults'
    warning: Optional[str]   # one short line, or None


def default_keywords() -> tuple:
    return tuple(k.lower() for k in Config.blacklist_keywords)


def parse(text: str) -> tuple:
    """File text -> lower-case keywords, comments/blank lines skipped, no duplicates."""
    words: list[str] = []
    for line in text.splitlines():
        word = line.strip().lower()
        if word and not word.startswith("#") and word not in words:
            words.append(word)
    return tuple(words)


def matches(title: str, keywords: Iterable[str]) -> bool:
    low = title.lower()
    return any(k in low for k in keywords)


def render(keywords: Iterable[str]) -> str:
    return HEADER + "\n" + "\n".join(keywords) + "\n"


def resolve_path(cli_path: Optional[str] = None,
                 environ: Optional[Mapping[str, str]] = None) -> Path:
    environ = os.environ if environ is None else environ
    if cli_path:
        return Path(cli_path)
    if environ.get(ENV_VAR):
        return Path(environ[ENV_VAR])
    return DEFAULT_PATH


def load(path: Path | str) -> LoadResult:
    """Read the list; never raises."""
    path = Path(path)
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render(default_keywords()), encoding="utf-8")
        except OSError as exc:
            return LoadResult(default_keywords(), path, "defaults",
                              f"could not create the block list at {path} ({type(exc).__name__}); "
                              "using the built-in list")
        return LoadResult(default_keywords(), path, "created", None)
    try:
        text = path.read_text(encoding="utf-8-sig")       # accepts a Notepad BOM
    except (OSError, UnicodeDecodeError) as exc:
        return LoadResult(default_keywords(), path, "defaults",
                          f"could not read the block list at {path} ({type(exc).__name__}); "
                          "using the built-in list")
    return LoadResult(parse(text), path, "file", None)


def make_loader(path: Path | str, warn=None, first: Optional[LoadResult] = None):
    """A zero-argument loader for RecallEngine(blocklist_loader=...).
    Each call re-reads the file; `warn(text)` receives any warning. If `first`
    is given (a result the caller already reported), the first call returns
    it without reading the file again, so a warning is printed only once."""
    pending = [first]

    def loader() -> tuple:
        result, pending[0] = pending[0], None
        if result is not None:
            return result.keywords
        result = load(path)
        if result.warning and warn:
            warn("warning: " + result.warning)
        return result.keywords
    return loader
