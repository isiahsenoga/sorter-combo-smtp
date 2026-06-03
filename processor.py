from __future__ import annotations
import logging
import os
import re
import time
import io
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Optional

from scanner import scan_files, is_combo_file, is_smtp_file, DEFAULT_KEYWORDS

logger = logging.getLogger(__name__)

_IO_WORKERS  = min(16, (os.cpu_count() or 4) * 2)
_IO_BUF      = 1 << 17   # 128 KB read/write buffer
_WRITE_BATCH = 20_000     # lines per write() call

# Compiled once at module load — not inside any function
_SPLIT_RE = re.compile(r"[:|,; \t]+")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{2,253}$)(?!-)(?!.*\.\.)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _valid_email_token(p: str) -> bool:
    """True if the token looks like user@domain.tld."""
    return bool(_EMAIL_RE.match(p.strip()))


def _pw_end(s: str) -> int:
    """Return index of first separator char in s, or len(s) if none found."""
    for i, c in enumerate(s):
        if c in ':|,; \t\r\n':
            return i
    return len(s)


# ── fast single-pass parsers ──────────────────────────────────────────────────

def _combo_full(line: str) -> tuple[str, str, str] | None:
    """
    Single-pass combo parse — no double work.
    Returns (canonical, key, domain) or None.
      canonical = "email:password"
      key       = lowercase email  (dedup key)
      domain    = email domain
    Fast path uses str.partition (C speed); regex fallback for edge cases.
    """
    s = line.strip()
    if not s or '@' not in s:
        return None

    # Fast path: token before first ':' is the email (email:pass format)
    ci = s.find(':')
    if ci > 0:
        left = s[:ci].strip()
        if _valid_email_token(left):
            email  = left.lower()
            right  = s[ci + 1:].lstrip()
            pw     = right[:_pw_end(right)].strip()
            domain = email.split('@', 1)[1]
            return f"{email}:{pw}", email, domain

    # Pipe separator
    pi = s.find('|')
    if 0 < pi < len(s) - 1:
        left = s[:pi].strip()
        if _valid_email_token(left):
            email  = left.lower()
            right  = s[pi + 1:].lstrip()
            pw     = right[:_pw_end(right)].strip()
            domain = email.split('@', 1)[1]
            return f"{email}:{pw}", email, domain

    # Fallback: split on any separator, scan for email token
    parts = _SPLIT_RE.split(s)
    for i, p in enumerate(parts):
        if _valid_email_token(p):
            email  = p.lower()
            pw     = parts[i + 1] if i + 1 < len(parts) else ''
            domain = email.split('@', 1)[1]
            return f"{email}:{pw}", email, domain
    return None


def _looks_like_host_token(p: str) -> bool:
    """True if token could reasonably be an SMTP host or hostname.

    Accepts:
    - Dotted domains (smtp.example.com)
    - Bare hostnames with letters, digits, hyphens, or underscores

    Rejects:
    - IPs
    - Empty or whitespace
    - Tokens with @
    - Pure numeric values
    - Single-character tokens
    - Tokens containing path separators
    """
    if not p or '@' in p or p.isdigit():
        return False
    if len(p) < 2:
        return False
    if any(sep in p for sep in ('/', '\\', ':')):
        return False

    if '.' in p:
        return bool(_HOSTNAME_RE.match(p))

    sanitized = p.replace('-', '').replace('_', '')
    return bool(sanitized.isalnum() and len(sanitized) >= 2)


def _smtp_full(line: str) -> tuple[str, str, str] | None:
    """
    Single-pass SMTP parse with strict validation.
    Returns (canonical, key, domain) or None.
      canonical = "host:port:email:password"
      key       = "email:host"  (dedup key)
      domain    = email domain
    
    Validation rules (strict):
    - Must have 4+ parts when split
    - Email must be valid format (user@domain.tld)
    - Port must be numeric and in range 1-65535
    - Host must be a valid domain/hostname
    - Password must be non-empty and >= 2 chars (not just "a" or single char)
    - Reject entries with empty/missing components
    """
    s = line.strip()
    if not s or '@' not in s:
        return None
    
    parts = _SPLIT_RE.split(s)
    if len(parts) < 4:
        return None

    # Find email token
    email_idx = next((i for i, p in enumerate(parts) if _valid_email_token(p)), None)
    if email_idx is None:
        return None

    email = parts[email_idx].lower()
    host = ""
    port = ""
    pw = ""

    # Primary pattern: host:port:email:password (most common)
    if email_idx >= 2:
        candidate_host = parts[email_idx - 2]
        candidate_port = parts[email_idx - 1]
        
        # Validate port: must be numeric, non-empty, in valid range
        if (candidate_port and candidate_port.isdigit() and 
            1 <= int(candidate_port) <= 65535 and 
            _looks_like_host_token(candidate_host)):
            host = candidate_host.lower()
            port = candidate_port
            pw = parts[email_idx + 1] if email_idx + 1 < len(parts) else ""

    # Fallback: find host before email, then port before host
    if not host:
        host_idx = next(
            (i for i, p in enumerate(parts[:email_idx]) if _looks_like_host_token(p)),
            None,
        )
        if host_idx is None:
            return None

        host = parts[host_idx].lower()
        
        # Find port: must be numeric and in valid range
        port = next(
            (
                parts[i]
                for i in range(host_idx + 1, email_idx)
                if parts[i].isdigit() and 1 <= int(parts[i]) <= 65535
            ),
            "",
        )
        
        # Get password from part after email
        if email_idx + 1 < len(parts):
            pw = parts[email_idx + 1]
        else:
            # Last resort: find any part that isn't host, port, or email
            pw = next(
                (
                    p for i, p in enumerate(parts)
                    if i not in {host_idx, email_idx} and p != port and p
                ),
                "",
            )

    # Strict validation: require all components
    if not host or not port or not email or not pw:
        return None
    
    # Password must be at least 2 chars (reject single char passwords like "a" or "1")
    if len(pw) < 2:
        return None
    
    # Reject obviously invalid passwords (too common placeholders)
    if pw.lower() in ("none", "null", "n/a", "na", "pass", "password", "pwd"):
        return None
    
    # Reject date-like values such as 5/13/1969 which are unlikely to be SMTP passwords.
    if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", pw):
        return None
    if "\\" in pw:
        return None

    domain = email.split('@', 1)[1]
    return f"{host}:{port}:{email}:{pw}", f"{email}:{host}", domain


# ── backward-compatible public wrappers ───────────────────────────────────────

def _split(line: str) -> list[str]:
    return [p for p in _SPLIT_RE.split(line.strip()) if p]


def parse_combo(line: str) -> tuple[str, str] | None:
    r = _combo_full(line)
    if not r:
        return None
    email = r[1]
    pw    = r[0][len(email) + 1:]
    return email, pw


def parse_smtp(line: str) -> tuple[str, str, str, str] | None:
    r = _smtp_full(line)
    if not r:
        return None
    parts = r[0].split(':', 3)
    return tuple(parts) if len(parts) == 4 else None  # type: ignore[return-value]


def normalize_key(line: str) -> str | None:
    r = _combo_full(line)
    return r[1] if r else None


def smtp_normalize_key(line: str) -> str | None:
    r = _smtp_full(line)
    return r[1] if r else None


def canonicalize_combo(line: str) -> str | None:
    r = _combo_full(line)
    return r[0] if r else None


def canonicalize_smtp(line: str) -> str | None:
    r = _smtp_full(line)
    return r[0] if r else None


def _extract_email(result: tuple[str, str, str] | None) -> str | None:
    if not result:
        return None
    key = result[1]
    if '@' in key:
        return key.split(':', 1)[0].lower()
    return None


def extract_domain(line: str) -> str | None:
    r = _combo_full(line)
    if r:
        return r[2]
    r = _smtp_full(line)
    return r[2] if r else None


# ── paths ─────────────────────────────────────────────────────────────────────

def _base() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _mode_folder(mode: str) -> str:
    return os.path.join(_base(), "data", mode)


def _master_db_path(mode: str = "combo") -> str:
    """Path to SQLite master database."""
    return os.path.join(_mode_folder(mode), "master.db" if mode == "combo" else "smtp.db")


def master_path(mode: str = "combo") -> str:
    """Returns path to master database (SQLite)."""
    os.makedirs(_mode_folder(mode), exist_ok=True)
    return _master_db_path(mode)


def _init_master_db(db_path: str) -> None:
    """Create or initialize master database with schema."""
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                key TEXT PRIMARY KEY,
                entry TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entry ON entries(entry)")
        conn.commit()
        conn.close()
        logger.info("Initialized master database: %s", db_path)
    except Exception as exc:
        logger.error("Failed to initialize master database: %s", exc)


def _load_all_keys_from_db(db_path: str) -> set:
    """Load all keys from SQLite database into memory set for fast dedup checks."""
    keys: set = set()
    if not os.path.exists(db_path):
        return keys
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        cursor = conn.execute("SELECT key FROM entries")
        for (key,) in cursor:
            keys.add(key)
        conn.close()
        logger.info("Loaded %d keys from database", len(keys))
    except Exception as exc:
        logger.warning("Failed to load keys from database: %s", exc)
    return keys


def _rebuild_db_from_old_master(
    old_master_path: str,
    db_path: str,
    key_fn: Callable[[str], str | None],
    status_cb: Optional[Callable[[str], None]] = None,
    pause_check: Optional[Callable[[], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> set:
    """Migrate an old plaintext master file into the SQLite master database."""
    keys: set[str] = set()
    if not os.path.exists(old_master_path):
        return keys

    def _status(msg: str) -> None:
        if status_cb:
            try:
                status_cb(msg)
            except Exception:
                pass

    _status("Rebuilding database from legacy master file…")
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        conn.execute("PRAGMA synchronous=NORMAL")
        _init_master_db(db_path)

        batch: list[tuple[str, str]] = []
        with open(old_master_path, "r", encoding="utf-8", errors="ignore") as infile:
            for line in infile:
                if cancel_check and cancel_check():
                    raise InterruptedError
                if pause_check:
                    while not pause_check():
                        time.sleep(0.1)

                key = key_fn(line)
                if not key or key in keys:
                    continue

                value = line.strip()
                if not value:
                    continue

                keys.add(key)
                batch.append((key, value))
                if len(batch) >= _WRITE_BATCH:
                    conn.executemany(
                        "INSERT OR REPLACE INTO entries (key, entry) VALUES (?, ?)",
                        batch,
                    )
                    conn.commit()
                    batch.clear()

        if batch:
            conn.executemany(
                "INSERT OR REPLACE INTO entries (key, entry) VALUES (?, ?)",
                batch,
            )
            conn.commit()

        logger.info("Rebuilt %d keys from legacy master into %s", len(keys), db_path)
    except InterruptedError:
        logger.info("Legacy master rebuild cancelled")
    except Exception as exc:
        logger.error("Failed to rebuild database from old master: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return keys


def fresh_output_path(mode: str = "combo", line_count: int = 0) -> str:
    d = os.path.join(_base(), "output")
    os.makedirs(d, exist_ok=True)
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "fresh" if mode == "combo" else "smtp_fresh"

    # Format line count as K (thousands) or M (millions)
    count_str = ""
    if line_count > 0:
        if line_count >= 1_000_000:
            count_str = f"_{line_count / 1_000_000:.1f}M"
        elif line_count >= 1_000:
            count_str = f"_{line_count / 1_000:.0f}K"
        else:
            count_str = f"_{line_count}"

    return os.path.join(d, f"{prefix}_{stamp}{count_str}_lines.txt")


def reports_dir() -> str:
    d = os.path.join(_base(), "reports")
    os.makedirs(d, exist_ok=True)
    return d


def output_dir(mode: str = "combo", emails_only: bool = False) -> str:
    d = os.path.join(_base(), "output")
    if emails_only:
        d = os.path.join(d, "email", mode)
    elif mode == "smtp":
        d = os.path.join(d, "smtp")
    else:
        d = os.path.join(d, "combo")
    os.makedirs(d, exist_ok=True)
    return d


# ── master key loading (fast-path + ETA) ─────────────────────────────────────

def load_master_keys(
    path: str,
    key_fn: Callable[[str], str | None],
    status_cb: Optional[Callable[[str], None]] = None,
    mode: str = "combo",
    pause_check: Optional[Callable[[], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> set:
    """
    Load dedup keys from SQLite database or rebuild from old .txt if needed.
    Returns set of all keys for O(1) duplicate checking during processing.
    """
    def _status(msg: str) -> None:
        if status_cb:
            try:
                status_cb(msg)
            except Exception:
                pass

    db_path = _master_db_path(mode)
    _init_master_db(db_path)
    
    t0 = time.monotonic()
    _status("Loading master keys from database…")
    keys = _load_all_keys_from_db(db_path)
    elapsed = time.monotonic() - t0
    
    if keys:
        _status(f"Loaded {len(keys):,} keys from database ({elapsed:.2f}s)")
        logger.info("Loaded %d keys in %.2fs from %s", len(keys), elapsed, db_path)
        return keys
    
    # Fallback: rebuild from old .txt master if it exists
    if os.path.exists(path) and path.endswith(".db"):
        # Path is already the database, nothing to migrate from
        return keys
    
    if os.path.exists(path):
        _status("Building database from existing master…")
        logger.info("Migrating from old master file: %s", path)
        return _rebuild_db_from_old_master(
            path,
            db_path,
            key_fn,
            status_cb=_status,
            pause_check=pause_check,
            cancel_check=cancel_check,
        )
    
    return keys


# ── parallel file reader ──────────────────────────────────────────────────────

def _read_file(filepath: str) -> tuple[str, list[str], str | None]:
    """
    Bulk-read entire file as bytes in one syscall, decode once, split into lines.
    Faster than Python line-by-line iteration for any file.
    """
    try:
        with open(filepath, "rb") as fh:
            data = fh.read()
        lines = [l for l in data.decode("utf-8", "ignore").splitlines() if l.strip()]
        return filepath, lines, None
    except OSError as exc:
        return filepath, [], str(exc)


# ── main processor ────────────────────────────────────────────────────────────

def process_dataset(
    folder: str,
    settings: dict | None = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    status_cb: Optional[Callable[[str], None]] = None,
    pause_check: Optional[Callable[[], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> dict:
    """
    Scan a folder, deduplicate against the master, and save new entries.

    settings keys
    -------------
    mode            "combo" | "smtp"
    keyword_filter  bool
    keywords        list[str]
    format_check    bool

    Returns dict: total_scanned, new_written, duplicates_skipped,
    already_in_master, format_skipped, domains, error_count,
    fresh_file, scanned_files, mode.
    """
    s         = settings or {}
    mode      = s.get("mode", "combo")
    kw_filter = s.get("keyword_filter", False)
    kws       = s.get("keywords", DEFAULT_KEYWORDS)
    fmt_check = s.get("format_check", True)
    parse_fn  = _smtp_full if mode == "smtp" else _combo_full

    def _status(msg: str) -> None:
        if status_cb:
            try:
                status_cb(msg)
            except Exception:
                pass

    # ── discover files ────────────────────────────────────────────────────────
    _status("Discovering files…")
    all_files = list(
        scan_files(
            folder,
            keyword_filter=kw_filter,
            keywords=kws,
            pause_check=pause_check,
            cancel_check=cancel_check,
        )
    )

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

    fmt_skipped: list[str] = []
    if fmt_check:
        _status(f"Checking format of {len(all_files)} file(s)…")
        kept = []
        for f in all_files:
            if mode == "smtp":
                valid = is_smtp_file(f)
            else:
                valid = is_combo_file(f)
            if valid:
                kept.append(f)
            else:
                fmt_skipped.append(f)
                logger.info("Format-skipped: %s", f)
        all_files = kept

    files_total = len(all_files)
    total_bytes = 0
    for f in all_files:
        try:
            total_bytes += os.path.getsize(f)
        except OSError:
            pass

    # ── load existing keys ────────────────────────────────────────────────────
    key_fn        = smtp_normalize_key if mode == "smtp" else normalize_key
    mpath         = master_path(mode)
    _status("Loading master keys…")
    existing_keys = load_master_keys(
        mpath,
        key_fn,
        status_cb=status_cb,
        mode=mode,
        pause_check=pause_check,
        cancel_check=cancel_check,
    )
    _status(f"Master loaded — {len(existing_keys):,} existing keys")

    # ── sequential file scan and dedup + batch write ─────────────────────────
    _status(f"Processing {files_total} file(s)…")
    read_errors: list[str] = []
    seen_run: set = set()
    domains = Counter()
    total = 0
    processed_bytes = 0
    new_written = 0
    dup_skip = 0
    already_in = 0
    _REPORT_EVERY = 50_000
    start_time = time.monotonic()

    # Use temp path, will rename after we know line count
    fpath_temp = fresh_output_path(mode, line_count=0)
    fpath = fpath_temp

    try:
        # Open SQLite database and fresh output file
        db_conn = sqlite3.connect(mpath, timeout=10.0)
        db_conn.execute("PRAGMA synchronous=NORMAL")
        _init_master_db(mpath)
        
        with open(fpath, "w", encoding="utf-8", buffering=_IO_BUF) as ff:
            mbatch: list[tuple[str, str]] = []  # (key, entry) pairs for database
            fbatch: list[str] = []

            def _flush() -> None:
                if mbatch:
                    db_conn.executemany(
                        "INSERT OR REPLACE INTO entries (key, entry) VALUES (?, ?)",
                        mbatch
                    )
                    db_conn.commit()
                    mbatch.clear()
                if fbatch:
                    ff.write("\n".join(fbatch) + "\n")
                    fbatch.clear()

            for done_count, fp in enumerate(all_files, start=1):
                _wait_if_paused()
                _check_cancel()
                if progress_cb:
                    try:
                        progress_cb(done_count, files_total, os.path.basename(fp))
                    except Exception:
                        pass

                file_bytes_read = 0
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        for line in fh:
                            _wait_if_paused()
                            _check_cancel()
                            total += 1
                            file_bytes_read += len(line)
                            current_bytes = processed_bytes + file_bytes_read

                            if total % _REPORT_EVERY == 0:
                                elapsed = max(time.monotonic() - start_time, 0.001)
                                rate = current_bytes / elapsed
                                if total_bytes:
                                    pct = int(current_bytes / total_bytes * 100)
                                    eta_s = int((total_bytes - current_bytes) / rate) if rate else 0
                                else:
                                    pct = int(done_count / files_total * 100) if files_total else 0
                                    eta_s = 0
                                eta_text = f"  ETA ~{eta_s}s" if eta_s else ""
                                _status(
                                    f"Deduplicating… file {done_count}/{files_total}  "
                                    f"{total:,} lines  ({pct}%)  —  {new_written:,} new{eta_text}"
                                )

                            result = parse_fn(line)
                            if result is None:
                                continue
                            clean, key, dom = result

                            if key in existing_keys:
                                already_in += 1
                                continue
                            if key in seen_run:
                                dup_skip += 1
                                continue

                            seen_run.add(key)
                            existing_keys.add(key)
                            mbatch.append((key, clean))
                            fbatch.append(clean)
                            new_written += 1
                            if dom:
                                domains[dom] += 1

                            if len(mbatch) >= _WRITE_BATCH:
                                _flush()
                except OSError as exc:
                    read_errors.append(f"{fp}: {exc}")
                finally:
                    processed_bytes += file_bytes_read

                if total > 0 and total % _REPORT_EVERY != 0:
                    elapsed = max(time.monotonic() - start_time, 0.001)
                    pct = int(processed_bytes / total_bytes * 100) if total_bytes else int(done_count / files_total * 100)
                    eta_s = int((total_bytes - processed_bytes) / (processed_bytes / elapsed)) if total_bytes and processed_bytes else 0
                    eta_text = f"  ETA ~{eta_s}s" if eta_s else ""
                    _status(
                        f"Deduplicating…  {total:,} lines  ({pct}%)  —  {new_written:,} new{eta_text}"
                    )

            _status(f"Writing output…  {new_written:,} new entries")
            _flush()
        
        db_conn.close()

    except OSError as exc:
        logger.error("Output write failed: %s", exc)
        read_errors.append(str(exc))
        try:
            db_conn.close()
        except Exception:
            pass
    except Exception as exc:
        logger.error("Unexpected error during processing: %s", exc)
        try:
            db_conn.close()
        except Exception:
            pass
        raise

    if new_written == 0:
        try:
            os.remove(fpath)
            fpath = ""
        except OSError:
            pass
    else:
        # Rename file to include line count
        try:
            final_fpath = fresh_output_path(mode, line_count=new_written)
            if fpath != final_fpath and os.path.exists(fpath):
                os.rename(fpath, final_fpath)
                fpath = final_fpath
        except OSError:
            pass

    return {
        "total_scanned":        total,
        "new_written":          new_written,
        "duplicates_skipped":   dup_skip,
        "already_in_master":    already_in,
        "format_skipped":       len(fmt_skipped),
        "format_skipped_files": fmt_skipped,
        "domains":              domains,
        "error_count":          len(read_errors),
        "fresh_file":           fpath,
        "scanned_files":        all_files,
        "mode":                 mode,
    }


# ── domain extractor ──────────────────────────────────────────────────────────

def _domain_matches(dom: str, query: str) -> bool:
    query = query.strip().lower()
    if not query:
        return False
    if query.startswith("@"):
        query = query[1:]
    if query.startswith("."):
        query = query[1:]
    return (
        dom == query or
        dom.endswith("." + query) or
        dom.startswith(query + ".")
    )


def extract_by_domain(
    query: str,
    mode: str = "combo",
    source_file: str | None = None,
    emails_only: bool = False,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> tuple[str, int]:
    """
    Extract all lines whose email domain matches query.
    query examples: "de", ".de", "@gmail.de", "gmail.com", ".net"
    If emails_only is True, write only the matching email addresses.
    Returns (output_filepath, count) or ("", 0).
    """
    query = query.strip().lower().lstrip("@").lstrip(".")
    if not query:
        return "", 0

    src = source_file or master_path(mode)
    if not os.path.exists(src):
        return "", 0

    is_combo = mode == "combo"
    parse_fn = _smtp_full if mode == "smtp" else _combo_full

    d     = output_dir(mode, emails_only)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_query = query.replace("@", "_at_").replace(".", "_")
    filename = "extract_emails_" if emails_only else "extract_"
    out   = os.path.join(d, f"{filename}{safe_query}_{stamp}.txt")
    seen: set[str] = set() if emails_only else set()

    try:
        total_size = os.path.getsize(src)
    except OSError:
        total_size = 0
    total_est = max(total_size // 50, 1)   # rough line count estimate

    count   = 0
    scanned = 0
    try:
        with (
            open(src, "r", encoding="utf-8", errors="ignore", buffering=_IO_BUF) as inf,
            open(out, "w", encoding="utf-8", buffering=_IO_BUF) as outf,
        ):
            batch: list[str] = []
            for raw in inf:
                scanned += 1
                line = raw.strip()
                if not line:
                    continue

                if is_combo:
                    if ':' not in line or '@' not in line:
                        if progress_cb and scanned % 50_000 == 0:
                            try:
                                progress_cb(scanned, total_est)
                            except Exception:
                                pass
                        continue

                    email = line.split(':', 1)[0].strip().lower()
                    if '@' not in email:
                        if progress_cb and scanned % 50_000 == 0:
                            try:
                                progress_cb(scanned, total_est)
                            except Exception:
                                pass
                        continue

                    domain = email.split('@', 1)[1]
                    if not _domain_matches(domain, query):
                        if progress_cb and scanned % 50_000 == 0:
                            try:
                                progress_cb(scanned, total_est)
                            except Exception:
                                pass
                        continue

                    if emails_only:
                        if email in seen:
                            if progress_cb and scanned % 50_000 == 0:
                                try:
                                    progress_cb(scanned, total_est)
                                except Exception:
                                    pass
                            continue
                        seen.add(email)
                        batch.append(email)
                    else:
                        batch.append(line)
                else:
                    r = parse_fn(line)
                    if not r or not _domain_matches(r[2], query):
                        if progress_cb and scanned % 50_000 == 0:
                            try:
                                progress_cb(scanned, total_est)
                            except Exception:
                                pass
                        continue

                    if emails_only:
                        email = _extract_email(r)
                        if not email:
                            continue
                        if email in seen:
                            if progress_cb and scanned % 50_000 == 0:
                                try:
                                    progress_cb(scanned, total_est)
                                except Exception:
                                    pass
                            continue
                        seen.add(email)
                        batch.append(email)
                    else:
                        batch.append(r[0])

                count += 1
                if len(batch) >= _WRITE_BATCH:
                    outf.write("\n".join(batch) + "\n")
                    batch.clear()

                if progress_cb and scanned % 50_000 == 0:
                    try:
                        progress_cb(scanned, total_est)
                    except Exception:
                        pass
            if batch:
                outf.write("\n".join(batch) + "\n")
    except OSError as exc:
        logger.error("extract_by_domain failed: %s", exc)
        return "", 0

    if count == 0:
        try:
            os.remove(out)
        except OSError:
            pass
        return "", 0

    return out, count


def extract_by_email(
    query: str,
    mode: str = "combo",
    source_file: str | None = None,
    emails_only: bool = False,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> tuple[str, int]:
    """
    Extract all lines whose email matches query (exact or partial).
    query examples: "user@gmail.com", "user", "@gmail.com", "john"
    If emails_only is True, write only the matching email addresses.
    Returns (output_filepath, count) or ("", 0).
    """
    query = query.strip().lower().lstrip("@")
    if not query:
        return "", 0

    src = source_file or master_path(mode)
    if not os.path.exists(src):
        return "", 0

    is_combo = mode == "combo"
    parse_fn = _smtp_full if mode == "smtp" else _combo_full

    d     = output_dir(mode, emails_only)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_query = query.replace("@", "_at_").replace(".", "_")
    filename = "extract_email_only_" if emails_only else "extract_email_"
    out   = os.path.join(d, f"{filename}{safe_query}_{stamp}.txt")
    seen: set[str] = set() if emails_only else set()

    try:
        total_size = os.path.getsize(src)
    except OSError:
        total_size = 0
    total_est = max(total_size // 50, 1)

    count   = 0
    scanned = 0
    try:
        with (
            open(src, "r", encoding="utf-8", errors="ignore", buffering=_IO_BUF) as inf,
            open(out, "w", encoding="utf-8", buffering=_IO_BUF) as outf,
        ):
            batch: list[str] = []
            for raw in inf:
                scanned += 1
                line = raw.strip()
                if not line:
                    continue

                if is_combo:
                    if ':' not in line or '@' not in line:
                        if progress_cb and scanned % 50_000 == 0:
                            try:
                                progress_cb(scanned, total_est)
                            except Exception:
                                pass
                        continue

                    email = line.split(':', 1)[0].strip().lower()
                    if query not in email:
                        if progress_cb and scanned % 50_000 == 0:
                            try:
                                progress_cb(scanned, total_est)
                            except Exception:
                                pass
                        continue

                    if emails_only:
                        if email in seen:
                            if progress_cb and scanned % 50_000 == 0:
                                try:
                                    progress_cb(scanned, total_est)
                                except Exception:
                                    pass
                            continue
                        seen.add(email)
                        batch.append(email)
                    else:
                        batch.append(line)
                else:
                    r = parse_fn(line)
                    email = _extract_email(r)
                    if not email or query not in email:
                        if progress_cb and scanned % 50_000 == 0:
                            try:
                                progress_cb(scanned, total_est)
                            except Exception:
                                pass
                        continue

                    if emails_only:
                        if email in seen:
                            if progress_cb and scanned % 50_000 == 0:
                                try:
                                    progress_cb(scanned, total_est)
                                except Exception:
                                    pass
                            continue
                        seen.add(email)
                        batch.append(email)
                    else:
                        batch.append(r[0])

                count += 1
                if len(batch) >= _WRITE_BATCH:
                    outf.write("\n".join(batch) + "\n")
                    batch.clear()
                if progress_cb and scanned % 50_000 == 0:
                    try:
                        progress_cb(scanned, total_est)
                    except Exception:
                        pass
            if batch:
                outf.write("\n".join(batch) + "\n")
    except OSError as exc:
        logger.error("extract_by_email failed: %s", exc)
        return "", 0

    if count == 0:
        try:
            os.remove(out)
        except OSError:
            pass
        return "", 0

    return out, count


def split_by_domains(
    source_file: str,
    queries: list[str],
    out_dir: str | None = None,
    mode: str = "combo",
    *,
    progress_cb: Optional[Callable[[dict], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    progress_interval: int = 50_000,
) -> dict[str, tuple[str, int]]:
    """
    Split ``source_file`` into per-domain files in a single pass.

    Parameters
    ----------
    source_file: str
        Path to the dataset to split.
    queries: list[str]
        Domain queries (gmail.com, rr.com, etc.).
    out_dir: str | None
        Optional custom output directory.
    mode: str
        "combo" | "smtp" — decides parser.
    progress_cb: Optional[Callable[[dict], None]]
        Invoked periodically with a stats payload containing:
        ``lines``, ``matches``, ``files_created``, ``processed_bytes``, ``total_bytes``,
        ``elapsed`` (seconds), ``speed_lpm``, ``eta_seconds`` and ``domain_counts`` (list of tuples).
    log_cb: Optional[Callable[[str], None]]
        Receives human-readable messages for UI log panels.
    progress_interval: int
        Minimum number of processed lines between ``progress_cb`` invocations.

    Returns
    -------
    dict[str, tuple[str, int]]
        mapping query → (output path, match count).
    """
    if not queries or not os.path.exists(source_file):
        return {}

    norm_queries = [q.strip().lower().lstrip("@").lstrip(".") for q in queries if q.strip()]
    norm_queries = list(dict.fromkeys(norm_queries))
    if not norm_queries:
        return {}

    parse_fn = _smtp_full if mode == "smtp" else _combo_full
    folder   = out_dir or os.path.join(_base(), "output", "split")
    os.makedirs(folder, exist_ok=True)

    handles: dict[str, io.TextIOWrapper] = {}
    batches: dict[str, list[str]] = {q: [] for q in norm_queries}
    counts:  dict[str, int]       = {q: 0 for q in norm_queries}
    paths:   dict[str, str]       = {}
    created: set[str]             = set()

    for q in norm_queries:
        safe = q.replace("/", "_").replace("\\", "_")
        p = os.path.join(folder, f"{safe}.txt")
        paths[q] = p
        handles[q] = open(p, "w", encoding="utf-8", buffering=_IO_BUF)

    def _flush_all() -> None:
        for q, batch in batches.items():
            if batch:
                handles[q].write("\n".join(batch) + "\n")
                batch.clear()

    try:
        total_bytes = os.path.getsize(source_file)
    except OSError:
        total_bytes = 0

    if log_cb:
        size_gb = total_bytes / (1024 ** 3) if total_bytes else 0
        size_str = f"{size_gb:.2f} GB" if total_bytes else "unknown size"
        log_cb(f"[INFO] Loading dataset: {os.path.basename(source_file)} ({size_str})")
        log_cb("[INFO] Split mode: multi-domain")
        log_cb(f"[INFO] Domains loaded: {len(norm_queries)}")
        log_cb("[INFO] Processing started")

    processed_lines = 0
    processed_bytes = 0
    matches_total   = 0
    last_emit_lines = 0
    start_time      = time.monotonic()

    try:
        with open(source_file, "r", encoding="utf-8", errors="ignore", buffering=_IO_BUF) as inf:
            for raw in inf:
                processed_lines += 1
                try:
                    processed_bytes = inf.tell()
                except (OSError, IOError):
                    processed_bytes += len(raw)

                line = raw.strip()
                if not line:
                    continue
                r = parse_fn(line)
                if not r:
                    continue
                dom = r[2]

                for q in norm_queries:
                    if not _domain_matches(dom, q):
                        continue
                    batches[q].append(r[0])
                    counts[q] += 1
                    matches_total += 1
                    if counts[q] == 1:
                        created.add(q)
                        if log_cb:
                            log_cb(f"[FILE] Created: {paths[q]}")
                    if len(batches[q]) >= _WRITE_BATCH:
                        handles[q].write("\n".join(batches[q]) + "\n")
                        batches[q].clear()

                if progress_cb and processed_lines - last_emit_lines >= progress_interval:
                    elapsed = max(time.monotonic() - start_time, 1e-6)
                    bytes_per_sec = processed_bytes / elapsed if processed_bytes else 0.0
                    eta_seconds = 0.0
                    if total_bytes and bytes_per_sec > 0:
                        remaining_bytes = max(total_bytes - processed_bytes, 0)
                        eta_seconds = remaining_bytes / bytes_per_sec if bytes_per_sec else 0.0
                    speed_lps = processed_lines / elapsed
                    top_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)
                    progress_cb({
                        "lines": processed_lines,
                        "matches": matches_total,
                        "files_created": len(created),
                        "processed_bytes": processed_bytes,
                        "total_bytes": total_bytes,
                        "elapsed": elapsed,
                        "speed_lpm": speed_lps * 60.0,
                        "eta_seconds": eta_seconds,
                        "domain_counts": top_counts[:50],
                    })
                    if log_cb:
                        log_cb(f"[SCAN] {processed_lines:,} lines scanned")
                    last_emit_lines = processed_lines

        _flush_all()
    except OSError as exc:
        logger.error("split_by_domains failed: %s", exc)
        if log_cb:
            log_cb(f"[ERROR] split_by_domains failed: {exc}")
    finally:
        for fh in handles.values():
            try:
                fh.close()
            except OSError:
                pass

    result: dict[str, tuple[str, int]] = {}
    for q in norm_queries:
        if counts[q] == 0:
            try:
                os.remove(paths[q])
            except OSError:
                pass
            result[q] = ("", 0)
        else:
            result[q] = (paths[q], counts[q])

    if progress_cb:
        elapsed = max(time.monotonic() - start_time, 1e-6)
        speed_lps = processed_lines / elapsed if elapsed else 0.0
        bytes_per_sec = processed_bytes / elapsed if processed_bytes else 0.0
        eta_seconds = 0.0
        if total_bytes and bytes_per_sec > 0:
            eta_seconds = max(total_bytes - processed_bytes, 0) / bytes_per_sec if bytes_per_sec else 0.0
        top_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        progress_cb({
            "lines": processed_lines,
            "matches": matches_total,
            "files_created": len(created),
            "processed_bytes": processed_bytes,
            "total_bytes": total_bytes,
            "elapsed": elapsed,
            "speed_lpm": speed_lps * 60.0,
            "eta_seconds": eta_seconds,
            "domain_counts": top_counts[:50],
        })

    if log_cb:
        log_cb("[INFO] Completed")

    return result


def split_folder_by_domains(
    folder: str,
    queries: list[str],
    out_dir: str | None = None,
    mode: str = "combo",
) -> dict[str, tuple[str, int]]:
    """
    Split every file under ``folder`` into per-domain files (single pass per file).
    Returns dict: query → (output_path, count).
    """
    if not queries or not os.path.isdir(folder):
        return {}

    norm_queries = [q.strip().lower().lstrip("@").lstrip(".") for q in queries if q.strip()]
    norm_queries = list(dict.fromkeys(norm_queries))
    if not norm_queries:
        return {}

    # Auto mode: process both combo and smtp subfolders (or data/<mode>)
    if mode == "auto":
        aggregate: dict[str, tuple[str, int]] = {}
        for m in ("combo", "smtp"):
            sub = os.path.join(folder, m)
            if not os.path.isdir(sub):
                sub = os.path.join(_base(), "data", m)
            if not os.path.isdir(sub):
                continue
            res = split_folder_by_domains(sub, norm_queries, out_dir=out_dir, mode=m)
            for q, (p, c) in res.items():
                key = f"{q} ({m})"
                aggregate[key] = (p, c)
        return aggregate

    parse_fn = _smtp_full if mode == "smtp" else _combo_full
    target   = out_dir or os.path.join(_base(), "output", "split")
    os.makedirs(target, exist_ok=True)

    handles: dict[str, io.TextIOWrapper] = {}
    batches: dict[str, list[str]] = {q: [] for q in norm_queries}
    counts:  dict[str, int]       = {q: 0  for q in norm_queries}
    paths:   dict[str, str]       = {}

    for q in norm_queries:
        safe = q.replace("/", "_").replace("\\", "_")
        suffix = f".{mode}" if mode else ""
        p = os.path.join(target, f"{safe}{suffix}.txt")
        paths[q] = p
        handles[q] = open(p, "w", encoding="utf-8", buffering=_IO_BUF)

    def _flush_all() -> None:
        for q, batch in batches.items():
            if batch:
                handles[q].write("\n".join(batch) + "\n")
                batch.clear()

    try:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                fp = os.path.join(root, name)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore", buffering=_IO_BUF) as inf:
                        for raw in inf:
                            line = raw.strip()
                            if not line:
                                continue
                            r = parse_fn(line)
                            if not r:
                                continue
                            dom = r[2]
                            for q in norm_queries:
                                if _domain_matches(dom, q):
                                    batches[q].append(r[0])
                                    counts[q] += 1
                                    if len(batches[q]) >= _WRITE_BATCH:
                                        handles[q].write("\n".join(batches[q]) + "\n")
                                        batches[q].clear()
                except OSError:
                    logger.error("split_folder_by_domains: could not read %s", fp)
        _flush_all()
    finally:
        for fh in handles.values():
            try:
                fh.close()
            except OSError:
                pass

    result: dict[str, tuple[str, int]] = {}
    for q in norm_queries:
        if counts[q] == 0:
            try:
                os.remove(paths[q])
            except OSError:
                pass
            result[q] = ("", 0)
        else:
            result[q] = (paths[q], counts[q])

    return result


# ── delete helper ─────────────────────────────────────────────────────────────

def delete_scanned_files(file_list: list[str]) -> tuple[int, int]:
    deleted = errors = 0
    for path in file_list:
        try:
            os.remove(path)
            deleted += 1
        except OSError as exc:
            errors += 1
            logger.error("Delete failed %s: %s", path, exc)
    return deleted, errors
