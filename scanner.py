from __future__ import annotations
import os
import re
import time
from typing import Callable, Generator, Optional

_EXTENSIONS = (".txt", ".csv", ".log")

DEFAULT_KEYWORDS = [
    "valid", "good", "mail", "combo", "access",
    "fresh", "checked", "new", "clean", "mailaccess",
    "cracked", "hit", "working",
]


def keyword_matches(filename: str, keywords: list[str]) -> bool:
    name = os.path.basename(filename).lower()
    return any(kw in name for kw in keywords)


def scan_files(
    folder: str,
    keyword_filter: bool = False,
    keywords: list[str] | None = None,
    pause_check: Optional[Callable[[], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Generator[str, None, None]:
    """
    Yield absolute paths of .txt/.csv/.log files found recursively.
    If keyword_filter=True, only yield files whose name contains one of keywords.
    """
    kws = keywords or DEFAULT_KEYWORDS
    stack = [folder]

    def _wait_if_paused() -> None:
        if pause_check:
            try:
                while not pause_check():
                    time.sleep(0.1)
            except Exception:
                pass

    def _check_cancel() -> None:
        if cancel_check and cancel_check():
            raise InterruptedError

    while stack:
        _wait_if_paused()
        _check_cancel()
        path = stack.pop()
        try:
            with os.scandir(path) as it:
                for entry in it:
                    _wait_if_paused()
                    _check_cancel()
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        name = entry.name.lower()
                        if not name.endswith(_EXTENSIONS):
                            continue
                        if keyword_filter and not keyword_matches(name, kws):
                            continue
                        yield entry.path
        except OSError:
            continue


def is_combo_file(
    filepath: str,
    sample_size: int = 100,
    threshold: float = 0.10,
) -> bool:
    """
    Sample up to sample_size non-empty lines.
    Returns True if >= threshold fraction look like email:pass combos
    (contain '@' and at least one common separator such as ':', '|', ',', ';', or whitespace).
    Returns False for unreadable files so they get skipped.
    """
    hits = 0
    checked = 0
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                checked += 1
                if "@" in line and _SEP_RE.search(line):
                    hits += 1
                if checked >= sample_size:
                    break
    except OSError:
        return False
    return checked > 0 and (hits / checked) >= threshold


_SEP_RE = re.compile(r"[:|,; \t]+")


def is_smtp_file(
    filepath: str,
    sample_size: int = 100,
    threshold: float = 0.10,
) -> bool:
    """
    Sample up to sample_size non-empty lines.
    Returns True if >= threshold fraction look like SMTP credentials
    (host:port:user@domain:pass or similar).
    """
    def _looks_like_email_token(p: str) -> bool:
        if "@" not in p:
            return False
        user, _, dom = p.partition("@")
        return bool(user) and "." in dom and not dom.startswith(".")

    hits = 0
    checked = 0
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                checked += 1
                parts = _SEP_RE.split(line)
                if len(parts) < 4:
                    if checked >= sample_size:
                        break
                    continue
                email_idx = next((i for i, p in enumerate(parts) if _looks_like_email_token(p)), None)
                if email_idx is None:
                    if checked >= sample_size:
                        break
                    continue
                if email_idx >= 2 and parts[email_idx - 1].isdigit():
                    hits += 1
                elif len(parts[0]) and parts[1].isdigit():
                    hits += 1
                if checked >= sample_size:
                    break
    except OSError:
        return False
    return checked > 0 and (hits / checked) >= threshold
