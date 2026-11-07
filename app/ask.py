"""Answer questions about the recent windows.

    result = ask("Which files did I open?", engine.snapshot(), backend, cfg)

1. A deterministic router answers list-type questions (which files / windows /
   apps did I use, what was I doing, the last window, how many windows) by
   listing the snapshot, newest first, with relative times. No model call.
2. Any other question goes to the local model with ONLY the numbered,
   masked title list and the question. The answer is checked: it must cite at
   least one valid item number, otherwise it becomes the "don't know" sentence;
   it is cut to three sentences.
Questions and answers are never written to disk.
"""
from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .redact import redact

DONT_KNOW = "I don't know from the recent windows."
REFUSAL = "I can only answer questions about your recent windows."
NO_WINDOWS = "No windows recorded yet."
MODEL_OFF = "The model is switched off (--no-model), so only list questions can be answered."
ASK_NUM_PREDICT = 96
MAX_SENTENCES = 3


@dataclass(frozen=True)
class AskResult:
    text: str
    source: str          # "list" | "model" | "refused" | "unknown"
    cited: tuple = ()    # item numbers (1 = newest window)


# ------------------------------------------------------------ helpers
def relative_time(ts: float, now: float) -> str:
    seconds = max(0, int(now - ts))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def _label(e) -> str:
    title = redact(e.title)
    return f"{title} ({e.app})" if e.app else title


def numbered_items(snapshot, now: float) -> list[str]:
    """'1. Contract Draft v2.txt (Notepad), 3 min ago', newest first, masked."""
    entries = list(snapshot.recent)[::-1]
    return [f"{i}. {_label(e)}, {relative_time(e.ts, now)}" for i, e in enumerate(entries, 1)]


def _normalize(question: str) -> str:
    q = question.lower().replace("’", "'")
    q = re.sub(r"[?!.,;:]+", " ", q)
    return " ".join(q.split())


_NOUNS = r"(?:documents?|docs?|files?|windows?|pages?|tabs?|apps?|applications?|programs?)"
_LAST = re.compile(r"\b(?:last|latest|most recent|previous) (?:window|file|document|doc|app|"
                   r"application|program|page|tab)\b|\bwhere (?:did|was) i (?:stop|leave)")
_COUNT = re.compile(r"\bhow many " + _NOUNS + r"\b")
_APPS = re.compile(r"^(?:which|what) (?:apps?|applications?|programs?)\b")
_LIST = re.compile(r"^(?:which|what) " + _NOUNS + r" (?:did|have|was|were|do) (?:i|you|they)\b"
                   r"|^(?:which|what) " + _NOUNS + r" (?:were|was|are) (?:open|opened|used)\b"
                   r"|^(?:list|show) (?:me )?(?:my |the )?(?:recent )?" + _NOUNS + r"\b"
                   r"|^what (?:was|were|have) i (?:been )?(?:doing|working on|up to)\b"
                   r"|^what did i (?:do|work on)\b")


def route(question: str) -> Optional[str]:
    """'last' | 'count' | 'apps' | 'list' for questions answered without a model."""
    q = _normalize(question)
    if _LAST.search(q):
        return "last"
    if _COUNT.search(q):
        return "count"
    if _APPS.search(q):
        return "apps"
    if _LIST.search(q):
        return "list"
    return None


def answer_from_list(kind: str, snapshot, now: float) -> AskResult:
    items = numbered_items(snapshot, now)
    if not items:
        return AskResult(NO_WINDOWS, "list")
    entries = list(snapshot.recent)[::-1]
    if kind == "last":
        e = entries[0]
        return AskResult(f"The last window was {_label(e)}, {relative_time(e.ts, now)} [1].",
                         "list", (1,))
    if kind == "count":
        distinct = len({(e.title, e.app) for e in entries})
        return AskResult(f"{distinct} different windows were recorded (newest first):\n"
                         + "\n".join(items), "list", tuple(range(1, len(items) + 1)))
    if kind == "apps":
        seen: dict[str, list[int]] = {}
        for i, e in enumerate(entries, 1):
            seen.setdefault(e.app or "Other", []).append(i)
        lines = [f"{app}: {', '.join(f'[{n}]' for n in nums)}" for app, nums in seen.items()]
        return AskResult("Apps in your recent windows (newest first):\n" + "\n".join(lines),
                         "list", tuple(range(1, len(items) + 1)))
    return AskResult("Your recent windows, newest first:\n" + "\n".join(items),
                     "list", tuple(range(1, len(items) + 1)))


# ------------------------------------------------------------ model path
def build_prompt(question: str, items: list[str]) -> str:
    return (
        "You answer questions about the user's recent computer windows, using ONLY the "
        "numbered list below. The list shows window names only, never what is inside a file.\n"
        "Rules:\n"
        f"- Answer in at most {MAX_SENTENCES} short sentences and cite the item numbers you "
        "used in square brackets. Example: You had Report.docx open [2].\n"
        "- If the question is about the user's files, documents, windows or work but the list "
        "does not show the answer (for example what is written inside a file), reply exactly: "
        f"{DONT_KNOW}\n"
        "- Only if the question has nothing to do with the user's computer work (general "
        f"knowledge, chat), reply exactly: {REFUSAL}\n\n"
        "Recent windows, newest first:\n" + "\n".join(items) + "\n\n"
        f"Question: {redact(question.strip())}\nAnswer:"
    )


_CITE = re.compile(r"\[(\d{1,3})\]|\b(?:item|items|no\.?|number|#)\s*(\d{1,3})\b", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def cited_numbers(text: str, count: int) -> tuple:
    found = set()
    for m in _CITE.finditer(text):
        n = int(m.group(1) or m.group(2))
        if 1 <= n <= count:
            found.add(n)
    return tuple(sorted(found))


def first_sentences(text: str, limit: int = MAX_SENTENCES) -> str:
    text = " ".join(text.split())
    parts = [p for p in _SENTENCE_END.split(text) if p]
    return " ".join(parts[:limit])


_WORDS = re.compile(r"[^\W_]+")
# words that make a question about the user's own work, even without a title word
_WORK_WORDS = {
    "i", "me", "my", "mine", "we", "our", "us", "window", "windows", "file", "files",
    "document", "documents", "doc", "docs", "app", "apps", "application", "program", "tab",
    "tabs", "page", "pages", "open", "opened", "working", "work", "worked", "doing", "did",
    "recap", "wrote", "write", "edit", "edited", "read", "notes", "folder", "screen",
}
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "is",
         "was", "what", "which", "who", "how", "when", "where", "why", "txt", "docx", "xlsx"}


def _similar(a: str, b: str) -> bool:
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)))


def related(question: str, names: list[str]) -> bool:
    """True if the question is about the user's work: a personal or work word, or a
    word shared with one of the listed titles ('supplier quote' ~ 'Supplier Quotes')."""
    words = {w for w in _WORDS.findall(question.lower().replace("\u2019", "'"))}
    if words & _WORK_WORDS:
        return True
    title_words = {w for n in names for w in _WORDS.findall(n.lower())
                   if len(w) >= 3 and w not in _STOP}
    return any(_similar(q, w) for q in words - _STOP for w in title_words)


def named_items(text: str, names: list[str]) -> tuple:
    """Item numbers whose full title (e.g. 'Contract Draft v2.txt') appears in text."""
    low = " ".join(text.lower().split())
    return tuple(i for i, n in enumerate(names, 1)
                 if len(n) >= 4 and " ".join(n.lower().split()) in low)


def check_answer(raw: str, count: int, names: Optional[list[str]] = None,
                 question: Optional[str] = None) -> AskResult:
    """Post-check of a model answer. `names` are the item titles (1 = newest): an
    answer without a citation that names an item's full title is cited for it.
    With `question`, an answer that is not a cited answer becomes the refusal
    sentence for an unrelated question and the "don't know" sentence otherwise."""
    result = _check(raw, count, names)
    if question is None or result.source == "model":
        return result
    if related(question, names or []):
        return AskResult(DONT_KNOW, "unknown")
    return AskResult(REFUSAL, "refused")


def _check(raw: str, count: int, names: Optional[list[str]]) -> AskResult:
    text = first_sentences(raw.strip().strip('"'))
    low = text.lower().replace("’", "'")
    if "i can only answer questions about your recent windows" in low:
        return AskResult(REFUSAL, "refused")
    if not text or "i don't know" in low or "i do not know" in low:
        return AskResult(DONT_KNOW, "unknown")
    cited = cited_numbers(text, count)
    if not cited and names and not _CITE.search(text):
        cited = named_items(text, names)
        if cited:
            text = text.rstrip() + " " + "".join(f"[{n}]" for n in cited)
    if not cited:
        return AskResult(DONT_KNOW, "unknown")
    return AskResult(text, "model", cited)


def _call(backend, prompt: str) -> str:
    params = inspect.signature(backend.generate_text).parameters
    kwargs = {k: v for k, v in (("temperature", 0.0), ("num_predict", ASK_NUM_PREDICT))
              if k in params}
    text, _metrics = backend.generate_text(prompt, **kwargs)
    return text


def ask(question: str, snapshot, backend, cfg: Config, now: Optional[float] = None) -> AskResult:
    """Never raises; errors come back as a short message (no traceback, no title)."""
    now = time.time() if now is None else now
    if not question or not question.strip():
        return AskResult("Type a question about your recent windows.", "unknown")
    kind = route(question)
    if kind:
        return answer_from_list(kind, snapshot, now)
    items = numbered_items(snapshot, now)
    if not items:
        return AskResult(NO_WINDOWS, "list")
    if not cfg.use_model:
        return AskResult(MODEL_OFF, "unknown")
    try:
        if backend is None:
            from .inference import get_backend
            backend = get_backend(cfg)
        raw = _call(backend, build_prompt(question, items))
    except Exception as exc:
        return AskResult(f"The local model could not answer ({type(exc).__name__}). "
                         "Is Ollama running?", "unknown")
    names = [redact(e.title) for e in list(snapshot.recent)[::-1]]
    return check_answer(raw, len(items), names, question)
