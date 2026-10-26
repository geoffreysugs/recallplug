"""Turns the recent window list into 3 short English bullets.

Bullet 1 is always a rule-based sentence about the last window. The local
model (backend chosen in cfg.backend, see app/inference/) writes bullet 2
(what the user was likely working on) and bullet 3 ("Next: ...") in one
call. Each model sentence is checked, tidied and, if too long, condensed
once; a sentence that fails is replaced by a rule-based one. Nothing is ever
cut off with "...". With cfg.use_model = False no model is called at all.
Timing numbers from every model call are saved to bench/metrics.csv.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .config import Config
from .inference import InferenceBackend, get_backend
from .titles import KNOWN_APPS, Entry

MAX_MODEL_CHARS = 90        # a model sentence longer than this is condensed once
CONDENSE_TARGET = 80        # what the condense prompt asks for

_BULLET_PREFIX = re.compile(r"^[\s\-\*•·●\d\.\)\(]+")
_CLOCK_TAG = re.compile(r"\[\s*\d{1,2}:\d{2}(?::\d{2})?\s*\]")   # [00:45] or [00:45:10]
_LINE_LEAD = re.compile(r"^(?:(?:answer|rewritten)\s*:\s*)?(?:(work|next)\s*:\s*)?", re.I)
_TRAILING_APP = re.compile(r"\s*\(([^()]{1,24})\)\s*([.!?]?)\s*$")

# Words that never count as the "main words" of a window title
_WORD = re.compile(r"[^\W_]+")
_MINOR_WORDS = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "at", "with", "by", "from",
    "txt", "doc", "docx", "xls", "xlsx", "csv", "ppt", "pptx", "pdf", "md", "py", "json", "html",
}
_ANCHOR_LABELS = ("last window:", "window before that:")
_TITLE_PARTS = re.compile(r"\s+[|\-–—·]\s+")   # ' | ', ' - ' or ' · '
_AT_BRANCH = re.compile(r"\s+at\s+\S+$")                     # GitHub: '<path> at main'
_HAS_EXT = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,4}\b")       # '.txt', '.py', '.docx'
_PATH_EDGE = "()[]{}<>,;:'\"“”"

# Worked example for the prompt. The titles are fake on purpose, so any
# line that mentions them (or repeats the example) is dropped.
EXAMPLE_TITLES = ("Example_Budget_Draft.xlsx", "Example Supplier Portal")
_EXAMPLE_WORK = "Work: You were collecting supplier prices for a budget."
_EXAMPLE = (
    "Example input:\n"
    "Last window: Example_Budget_Draft.xlsx (Excel)\n"
    "Window before that: Example Supplier Portal (Chrome)\n"
    "All recent windows, oldest first:\n"
    "1. Example Supplier Portal (Chrome)\n"
    "2. Example_Budget_Draft.xlsx (Excel)\n"
    "Example answer:\n"
    f"{_EXAMPLE_WORK}\n"
    "Next: update Example_Budget_Draft.xlsx with the supplier prices\n"
)


# ----------------------------------------------------------------- prompts
def window_label(title: str, app: str) -> str:
    return f"{title} ({app})" if app else title


def _anchors(entries: list[Entry]) -> str:
    if not entries:
        return ""
    last = entries[-1]
    text = f"Last window: {window_label(last.title, last.app)}\n"
    if len(entries) > 1:
        prev = entries[-2]
        text += f"Window before that: {window_label(prev.title, prev.app)}\n"
    return text


def build_prompt(entries: list[Entry]) -> str:
    lines = [f"{i}. {window_label(e.title, e.app)}" for i, e in enumerate(entries, 1)]
    windows = "\n".join(lines) if lines else "(no activity captured)"
    return (
        "You are a concise workplace assistant. The user just came back to their desk.\n"
        f"Write exactly 2 lines in English, each under {CONDENSE_TARGET} characters.\n"
        "Line 1 starts with 'Work: ' and is one sentence about what the user was likely "
        "working on, judging from ALL recent windows. Name the theme or task; do not copy "
        "any window title.\n"
        "Line 2 starts with 'Next: ' and is one concrete step that continues the work shown "
        "(for example review, compare, update, finish, reply). It must name the last window "
        "or the window before that.\n"
        "Do not guess irreversible actions (deleting, uninstalling, sending, paying, "
        "submitting) unless the window titles clearly show it. Do not copy the window list. "
        "No other text.\n\n"
        + _EXAMPLE
        + "\nNow the real data:\n" + _anchors(entries)
        + "All recent windows, oldest first:\n" + windows + "\nAnswer:\n"
    )


def condense_prompt(sentence: str) -> str:
    return (
        f"Rewrite this sentence in under {CONDENSE_TARGET} characters, keeping the meaning "
        "and any file or page name. Reply with the rewritten sentence only.\n\n"
        f"Sentence: {sentence}\nRewritten:"
    )


# ------------------------------------------------------------ clean-up
def cap_text(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _comparable(text: str) -> str:
    """Lower-case, single-spaced, no bullet marker or end punctuation."""
    text = _BULLET_PREFIX.sub("", text)
    return " ".join(text.lower().split()).rstrip(".!?:;,")


def sanitize_bullets(raw: str, cfg: Config, entries: list[Entry] = (),
                     dropped: list[str] | None = None, cap: bool = True) -> list[str]:
    """Model output -> up to 3 clean one-line bullets. Never raw JSON, clock
    tags, a bare copy of a window title, an echoed anchor line, or a copy of
    the prompt example. Pass a list as `dropped` to collect why lines were
    dropped (for --debug). cap=False keeps long lines whole."""
    copies = {_comparable(e.title) for e in entries}
    copies |= {_comparable(window_label(e.title, e.app)) for e in entries}
    example = [t.lower() for t in EXAMPLE_TITLES]
    example_line = _comparable(_EXAMPLE_WORK)

    def drop(why: str, line: str) -> None:
        if dropped is not None and line.strip():
            dropped.append(f"{why}: {line.strip()}")

    bullets: list[str] = []
    for line in raw.splitlines():
        if "{" in line or "}" in line:
            drop("looks like JSON", line)
            continue
        line = line.replace("**", "").replace("`", "")
        line = _CLOCK_TAG.sub(" ", line)
        line = _BULLET_PREFIX.sub("", line).strip()
        if len(line) < 3:
            drop("too short", line)
            continue
        if _comparable(line) in copies:
            drop("bare copy of a window title", line)
            continue
        if line.lower().startswith(_ANCHOR_LABELS):
            drop("echoed anchor line", line)
            continue
        if any(t in line.lower() for t in example) or _comparable(line) == example_line:
            drop("copied from the prompt example", line)
            continue
        bullets.append(cap_text(line, cfg.max_bullet_chars) if cap else " ".join(line.split()))
        if len(bullets) == cfg.max_bullets:
            break
    return bullets


def main_words(title: str) -> list[str]:
    """'Contract Draft v2.txt' -> ['contract', 'draft', 'v2']."""
    words: list[str] = []
    for w in _WORD.findall(title.lower()):
        if len(w) >= 2 and w not in _MINOR_WORDS and w not in words:
            words.append(w)
    return words


def mentions(bullet: str, title: str) -> bool:
    """True if the bullet contains at least half of the title's main words."""
    wanted = set(main_words(title))
    if not wanted:
        return True
    found = wanted & set(_WORD.findall(bullet.lower()))
    return len(found) * 2 >= len(wanted)


def short_title(title: str) -> str:
    """'Extensions for X | Visual Studio Marketplace' -> 'Extensions for X',
    'repo/tests/a.py at main · owner/repo' -> 'repo/tests/a.py'."""
    first = _TITLE_PARTS.split(title, 1)[0].strip() or title
    m = _AT_BRANCH.search(first)
    if m:
        before = first[: m.start()].strip()
        if before and ("/" in before or _HAS_EXT.search(before)):
            return before
    return first


def file_names(title: str) -> set[str]:
    """File names in the window's short title, lower-case: tokens like
    'name.ext' and the last segment of a path ('repo/tests' -> 'tests')."""
    names = set()
    for token in short_title(title).split():
        token = token.strip(_PATH_EDGE).rstrip("/\\")
        last = re.split(r"[/\\]", token)[-1]
        is_path = last != token
        if len(last) >= 3 and (_HAS_EXT.search(last) or is_path):
            names.add(last.lower())
    return names


def names_file(text: str, title: str) -> bool:
    """True if text contains one of the window's file names (case-insensitive)."""
    low = text.lower()
    return any(re.search(r"(?<![\w.])" + re.escape(n) + r"(?!\w)", low) for n in file_names(title))


def rule_sentence(lead: str, e: Entry, limit: int, with_app: bool = True) -> str:
    """lead + short title (+ ' (App)'), trimmed at a word so it never needs '...'."""
    title = short_title(e.title)
    tail = f" ({e.app})" if with_app and e.app else ""
    room = max(1, limit - len(lead) - len(tail))
    if len(title) > room:
        cut = title[:room]
        title = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:-|")
    return lead + title + tail


def tidy_titles(text: str, entries: list[Entry]) -> str:
    """Full window titles -> short titles, and a trailing '(App)' is dropped."""
    for e in sorted(entries, key=lambda e: len(e.title), reverse=True):
        short = short_title(e.title)
        for full in (window_label(e.title, e.app), e.title):
            if full != short:
                text = re.sub(re.escape(full), lambda _m: short, text, flags=re.I)
    apps = {a.lower() for a in KNOWN_APPS.values()} | {e.app.lower() for e in entries if e.app}
    m = _TRAILING_APP.search(text)
    if m and m.group(1).strip().lower() in apps:
        text = text[: m.start()] + m.group(2)
    return " ".join(text.split())


def risky_words(text: str, cfg: Config) -> list[str]:
    """cfg.risky_next_words found in text as whole words (case-insensitive)."""
    if not cfg.risky_next_words:
        return []
    pattern = r"\b(?:" + "|".join(re.escape(w) for w in cfg.risky_next_words) + r")\b"
    return [w.lower() for w in re.findall(pattern, text, flags=re.I)]


# ------------------------------------------------------------ checks
def check_work(body: str, entries: list[Entry]) -> str | None:
    """Bullet 2 (raw model text). Returns why it fails, or None if it passes."""
    low = body.lower()
    for e in entries:
        if e.title.lower() in low:
            return "it copies a full window title"
    known = {w for e in entries for w in main_words(e.title)}
    if not known & set(_WORD.findall(low)) and not any(names_file(body, e.title) for e in entries):
        return "it shares no main word with any recent window"
    return None


def names_any(text: str, windows: list[Entry]) -> bool:
    """Main-word rule (full or short title) or the window's file name."""
    return any(mentions(text, e.title) or mentions(text, short_title(e.title))
               or names_file(text, e.title) for e in windows)


def names_last_two(text: str, entries: list[Entry]) -> bool:
    """The old strict rule: text names the last window or the one before it."""
    return names_any(text, entries[-2:])


def check_next(text: str, entries: list[Entry], cfg: Config) -> str | None:
    """Bullet 3 (tidied). Returns why it fails, or None if it passes."""
    risky = risky_words(text, cfg)
    if risky:
        return f"it suggests a risky action ({', '.join(risky)})"
    n = max(1, cfg.next_ground_windows)
    targets = entries[-n:]
    if not names_any(text, targets):
        needs = " or ".join(str(main_words(short_title(e.title))) for e in reversed(targets))
        return f"it names none of the last {n} windows (needs half of {needs})"
    return None


def split_answer(raw: str, cfg: Config, entries: list[Entry],
                 dropped: list[str] | None = None) -> tuple[str | None, str | None]:
    """Model answer -> (bullet 2 body, bullet 3 body), labels removed."""
    work = nxt = None
    rest: list[str] = []
    for line in sanitize_bullets(raw, cfg, entries, dropped, cap=False):
        m = _LINE_LEAD.match(line)
        kind = (m.group(1) or "").lower()
        body = line[m.end():].strip()
        if kind == "next" and nxt is None:
            nxt = body
        elif kind == "work" and work is None:
            work = body
        else:
            rest.append(body)
    if work is None and rest:
        work = rest.pop(0)
    if nxt is None and rest:
        nxt = rest.pop(0)
    return work, nxt


# ------------------------------------------------------------ the 3 bullets
def first_bullet(entries: list[Entry], cfg: Config) -> str:
    """Bullet 1: always rule-based."""
    return rule_sentence("You stopped in ", entries[-1], cfg.max_bullet_chars)


def rule_work(entries: list[Entry], cfg: Config) -> str | None:
    """Rule-based bullet 2 (None when there is no previous window)."""
    if len(entries) < 2:
        return None
    return rule_sentence("Before that: ", entries[-2], cfg.max_bullet_chars, with_app=False)


def default_next(entries: list[Entry], cfg: Config) -> str:
    """Rule-based bullet 3."""
    return rule_sentence("Next: continue in ", entries[-1], cfg.max_bullet_chars, with_app=False)


def fallback_bullets(entries: list[Entry], cfg: Config) -> list[str]:
    """Fully rule-based summary (no model, or the model is unavailable)."""
    if not entries:
        return ["No recent activity was captured before you left."]
    bullets = [first_bullet(entries, cfg), rule_work(entries, cfg), default_next(entries, cfg)]
    return [b for b in bullets if b]


def _show(kind: str, body: str) -> str:
    body = body.strip()
    if kind == "next":
        return "Next: " + body
    return body[:1].upper() + body[1:]


def _model_sentence(kind: str, body: str | None, entries: list[Entry], cfg: Config,
                    backend: InferenceBackend, debug: dict) -> tuple[str | None, int]:
    """Checks, tidies and (once) condenses one model sentence.
    Returns (sentence or None if it must be replaced, condense retries used)."""
    n = 2 if kind == "work" else 3

    def reject(why: str) -> None:
        debug["replaced"].append(f"bullet {n} replaced: {why}")

    def check(raw_body: str) -> tuple[str, str | None]:
        why = check_work(raw_body, entries) if kind == "work" else None
        text = _show(kind, tidy_titles(raw_body, entries))
        if why is None and kind == "next":
            why = check_next(text, entries, cfg)
        return text, why

    if not body:
        reject("the model gave no usable line")
        return None, 0
    text, why = check(body)
    if why:
        reject(f"{why}: {body}")
        return None, 0
    if len(text) <= MAX_MODEL_CHARS:
        return text, 0

    debug["retries"].append(f"bullet {n} condensed ({len(text)} characters): {text}")
    try:
        raw, _metrics = backend.generate_text(condense_prompt(text))
    except Exception as exc:
        reject(f"the condense retry failed ({type(exc).__name__})")
        return None, 1
    lines = sanitize_bullets(raw, cfg, entries, cap=False)
    if not lines:
        reject("the condensed answer was not usable")
        return None, 1
    text, why = check(_LINE_LEAD.sub("", lines[0], count=1))
    if why:
        reject(f"after condensing, {why}: {text}")
        return None, 1
    if len(text) > MAX_MODEL_CHARS:
        reject(f"still {len(text)} characters after one retry: {text}")
        return None, 1
    return text, 1


# ------------------------------------------------------------ metrics file
def log_metrics(path: str, metrics: dict) -> None:
    """Append one row to the CSV (creates the file and header if needed).

    A file whose header differs from these columns is first renamed to
    <name>_first_runs.csv (or _first_runs_2.csv, ...) so nothing is lost.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fields = list(metrics.keys())
    if p.exists() and p.stat().st_size > 0:
        with open(p, newline="", encoding="utf-8") as f:
            header = next(csv.reader(f), [])
        if header != fields:
            old = p.with_name(f"{p.stem}_first_runs{p.suffix}")
            n = 2
            while old.exists():
                old = p.with_name(f"{p.stem}_first_runs_{n}{p.suffix}")
                n += 1
            p.rename(old)
    new_file = not p.exists() or p.stat().st_size == 0
    with open(p, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            writer.writeheader()
        writer.writerow(metrics)


# ------------------------------------------------------------ public API
def summarize(entries: list[Entry], cfg: Config,
              backend: InferenceBackend | None = None) -> dict:
    """Returns {'bullets': [...], 'source': 'model'|'mixed'|'fallback'|'rule_only',
    'metrics': dict|None, 'debug': dict}.

    'model' means bullets 2 and 3 both came from the model, 'mixed' that at
    least one was replaced by a rule-based sentence, 'fallback' that the model
    was unreachable and 'rule_only' that cfg.use_model is False.
    metrics['bullets_replaced'] is 0-2, metrics['condense_retries'] 0-2 and
    metrics['next_names_last_two'] 'true'/'false' ('' if bullet 3 was replaced)
    and metrics['cpu_only'] 'true'/'false' (cfg.cpu_only).
    'debug' holds the raw model answer and the reasons; it is only printed
    (--debug / --show-rejections) and never written to a file.
    """
    debug: dict = {"backend": None, "raw": None, "dropped": [], "replaced": [], "retries": []}
    if not entries:
        return {"bullets": fallback_bullets(entries, cfg), "source": "fallback", "metrics": None,
                "debug": debug}
    if not cfg.use_model:
        debug["backend"] = "none"
        return {"bullets": fallback_bullets(entries, cfg), "source": "rule_only", "metrics": None,
                "debug": debug}

    backend = backend or get_backend(cfg)
    debug["backend"] = backend.name
    try:
        text, metrics = backend.generate_text(build_prompt(entries))
    except Exception as exc:
        print(f"[summary] {backend.name} not reachable ({type(exc).__name__}) - using fallback")
        return {"bullets": fallback_bullets(entries, cfg), "source": "fallback", "metrics": None,
                "debug": debug}

    debug["raw"] = text
    work, nxt = split_answer(text, cfg, entries, debug["dropped"])
    b2, r2 = _model_sentence("work", work, entries, cfg, backend, debug)
    b3, r3 = _model_sentence("next", nxt, entries, cfg, backend, debug)
    bullets = [first_bullet(entries, cfg), b2 or rule_work(entries, cfg),
               b3 or default_next(entries, cfg)]
    replaced = (b2 is None) + (b3 is None)
    used = "model" if replaced == 0 else "mixed"
    metrics["output_used"] = used
    metrics["bullets_replaced"] = replaced
    metrics["condense_retries"] = r2 + r3
    # only the boolean is saved: would the accepted bullet 3 pass the strict rule?
    # ("" when the model's bullet 3 was replaced)
    metrics["next_names_last_two"] = str(names_last_two(b3, entries)).lower() if b3 else ""
    metrics["cpu_only"] = str(cfg.cpu_only).lower()
    if cfg.metrics_csv:
        try:
            log_metrics(cfg.metrics_csv, metrics)
        except OSError:
            pass
    return {"bullets": [b for b in bullets if b], "source": used, "metrics": metrics,
            "debug": debug}
