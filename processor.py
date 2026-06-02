from __future__ import annotations
import logging
import os
import re
import time
import io
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


# ── helpers ───────────────────────────────────────────────────────────────────

def _valid_email_token(p: str) -> bool:
    """True if the token looks like user@domain.tld."""
    at = p.rfind('@')
    if at <= 0:
        return False
    dom = p[at + 1:]
    return '.' in dom and not dom.startswith('.') and len(dom) > 2


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
    
    Must be either:
    - A domain with at least one dot (mail.example.com)
    - A qualified hostname pattern (smtp, mail-server)
    
    Rejects:
    - IPs (handled separately)
    - Empty or whitespace
    - Tokens with @
    - Pure numeric
    - Single lowercase letters
    """
    if not p or '@' in p or p.isdigit():
        return False
    
    # Prefer domains with dots (most common case)
    if '.' in p:
        parts = p.split('.')
        # Reject if any part is empty: "example..com" or ".example.com"
        return all(part and len(part) > 0 for part in parts)
    
    # For bare hostnames, require at least 2 chars and alphanumeric + hyphens/underscores
    # But reject single letters or very short generic terms
    if len(p) < 2:
        return False
    
    sanitized = p.replace("-", "").replace("_", "")
    return sanitized.isalnum() and len(sanitized) >= 2


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


def master_path(mode: str = "combo") -> str:
    base_data   = os.path.join(_base(), "data")
    mode_folder = _mode_folder(mode)
    fname = "master.txt" if mode == "combo" else "smtp_master.txt"

    preferred = os.path.join(mode_folder, fname)
    legacy    = os.path.join(base_data, fname)

    for p in (preferred, legacy):
        if os.path.exists(p):
            return p

    os.makedirs(mode_folder, exist_ok=True)
    return preferred


def _keys_path(mode: str = "combo") -> str:
    """Keys index file — stores only dedup keys for ultra-fast loading."""
    base_data   = os.path.join(_base(), "data")
    mode_folder = _mode_folder(mode)
    fname = "master_keys.txt" if mode == "combo" else "smtp_keys.txt"

    preferred = os.path.join(mode_folder, fname)
    legacy    = os.path.join(base_data, fname)

    for p in (preferred, legacy):
        if os.path.exists(p):
            return p

    os.makedirs(mode_folder, exist_ok=True)
    return preferred


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


# ── master key loading (fast-path + ETA) ─────────────────────────────────────

def _load_chunk(path: str, start: int, end: int) -> frozenset:
    """Load a chunk of the keys file (for parallel loading)."""
    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(end - start)
        keys = frozenset(
            l.decode("utf-8", "ignore") for l in data.split(b"\n")
            if l.strip()
        )
        return keys
    except Exception:
        return frozenset()


def _load_keys_file(
    kpath: str,
    _status: Callable[[str], None],
    pause_check: Optional[Callable[[], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> set:
    """
    Fast path: load the pre-built keys index file.
    Uses parallel chunk loading for files > 10MB (CPU-count workers).
    No parsing — each line IS already the dedup key.
    """
    try:
        total_size = os.path.getsize(kpath)
    except OSError:
        total_size = 0

    t0 = time.monotonic()

    # For large files, use parallel chunk loading
    if total_size > 10 * 1024 * 1024:  # > 10 MB
        _status(f"Loading keys ({total_size // 1024 // 1024} MB) in parallel…")
        cpu_count = max(2, (os.cpu_count() or 4) - 1)
        chunk_size = total_size // cpu_count
        chunks = []
        for i in range(cpu_count):
            start = i * chunk_size
            end = total_size if i == cpu_count - 1 else (i + 1) * chunk_size
            chunks.append((kpath, start, end))

        keys: set = set()
        try:
            with ProcessPoolExecutor(max_workers=cpu_count) as ex:
                for chunk_keys in ex.map(_load_chunk, *zip(*chunks)):
                    keys.update(chunk_keys)
                    if cancel_check and cancel_check():
                        raise InterruptedError
                    elapsed = time.monotonic() - t0
                    pct = int(len(keys) / max(total_size // 100, 1))
                    _status(f"Loading keys…  {len(keys):,}  elapsed {elapsed:.1f}s")
        except InterruptedError:
            raise
        except Exception:
            # Fallback to sequential if multiprocessing fails
            keys = _load_keys_sequential(kpath, _status, t0, total_size, pause_check=pause_check, cancel_check=cancel_check)
        else:
            elapsed = time.monotonic() - t0
            _status(f"Keys loaded — {len(keys):,} unique keys  ({elapsed:.1f}s)")
            logger.info("Loaded %d keys in %.1fs from %s (parallel)", len(keys), elapsed, kpath)
            return keys
    else:
        keys = _load_keys_sequential(
            kpath,
            _status,
            t0,
            total_size,
            pause_check=pause_check,
            cancel_check=cancel_check,
        )

    return keys


def _load_keys_sequential(
    kpath: str,
    _status: Callable[[str], None],
    t0: float,
    total_size: int,
    pause_check: Optional[Callable[[], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> set:
    """Sequential key loading (fallback and for small files)."""
    CHUNK      = 1 << 22   # 4 MB
    keys: set  = set()
    partial    = b""
    bytes_read = 0

    try:
        with open(kpath, "rb") as fh:
            while True:
                if pause_check:
                    try:
                        while not pause_check():
                            time.sleep(0.1)
                    except Exception:
                        pass
                if cancel_check and cancel_check():
                    raise InterruptedError
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                data    = partial + chunk
                lines   = data.split(b"\n")
                partial = lines[-1]
                for ln in lines[:-1]:
                    k = ln.strip()
                    if k:
                        keys.add(k.decode("utf-8", "ignore"))
                bytes_read += len(chunk)
                elapsed = max(time.monotonic() - t0, 0.001)
                rate    = bytes_read / elapsed
                eta_s   = (total_size - bytes_read) / rate if rate > 0 else 0
                pct     = int(bytes_read / total_size * 100) if total_size else 0
                _status(
                    f"Loading keys…  {len(keys):,}  ({pct}%)"
                    f"  elapsed {elapsed:.1f}s  ETA ~{eta_s:.0f}s"
                )
        if partial.strip():
            keys.add(partial.strip().decode("utf-8", "ignore"))
        elapsed = time.monotonic() - t0
        _status(f"Keys loaded — {len(keys):,} unique keys  ({elapsed:.1f}s)")
        logger.info("Loaded %d keys in %.1fs from %s (sequential)", len(keys), elapsed, kpath)
    except OSError as exc:
        logger.warning("Could not read keys file: %s", exc)
    return keys


def _load_from_master(
    mpath: str,
    kpath: str,
    key_fn: Callable[[str], str | None],
    _status: Callable[[str], None],
    pause_check: Optional[Callable[[], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> set:
    """
    Slow path: parse master.txt to build the keys set.
    Simultaneously writes the keys index for fast loading on future runs.
    Shows progress + ETA via _status.
    """
    try:
        total_size = os.path.getsize(mpath)
    except OSError:
        total_size = 0

    t0         = time.monotonic()
    keys: set  = set()
    bytes_read = 0
    count      = 0
    _REPORT    = 500_000

    try:
        with (
            open(mpath, "r", encoding="utf-8", errors="ignore", buffering=_IO_BUF) as fh,
            open(kpath, "w", encoding="utf-8", buffering=_IO_BUF) as kf,
        ):
            kbatch: list[str] = []
            for raw in fh:
                if pause_check:
                    try:
                        while not pause_check():
                            time.sleep(0.1)
                    except Exception:
                        pass
                if cancel_check and cancel_check():
                    raise InterruptedError
                bytes_read += len(raw)
                line = raw.strip()
                if line:
                    k = key_fn(line)
                    if k:
                        keys.add(k)
                        kbatch.append(k)
                        if len(kbatch) >= _WRITE_BATCH:
                            kf.write("\n".join(kbatch) + "\n")
                            kbatch.clear()
                count += 1
                if count % _REPORT == 0:
                    elapsed = max(time.monotonic() - t0, 0.001)
                    rate    = bytes_read / elapsed
                    eta_s   = (total_size - bytes_read) / rate if rate > 0 else 0
                    pct     = int(bytes_read / total_size * 100) if total_size else 0
                    _status(
                        f"Building keys index…  {count:,} lines  ({pct}%)"
                        f"  ETA ~{eta_s:.0f}s"
                    )
            if kbatch:
                kf.write("\n".join(kbatch) + "\n")
        elapsed = time.monotonic() - t0
        _status(f"Keys indexed — {len(keys):,} unique keys  ({elapsed:.1f}s)")
        logger.info("Built keys index: %d keys in %.1fs from %s", len(keys), elapsed, mpath)
    except OSError as exc:
        logger.warning("Could not load master: %s", exc)
    return keys


def load_master_keys(
    path: str,
    key_fn: Callable[[str], str | None],
    status_cb: Optional[Callable[[str], None]] = None,
    mode: str = "combo",
    pause_check: Optional[Callable[[], bool]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> set:
    """
    Load dedup keys for the given mode.
    Fast path: reads a pre-built keys index file (no parsing, just strip+add).
    Slow path: parses master.txt and builds the keys index for next time.
    Both paths report progress + ETA via status_cb.
    """
    def _status(msg: str) -> None:
        if status_cb:
            try:
                status_cb(msg)
            except Exception:
                pass

    if not os.path.exists(path):
        return set()

    kpath = _keys_path(mode)
    if os.path.exists(kpath):
        return _load_keys_file(
            kpath,
            _status,
            pause_check=pause_check,
            cancel_check=cancel_check,
        )

    return _load_from_master(
        path,
        kpath,
        key_fn,
        _status,
        pause_check=pause_check,
        cancel_check=cancel_check,
    )


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
    kpath         = _keys_path(mode)
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
        with (
            open(mpath, "a", encoding="utf-8", buffering=_IO_BUF) as mf,
            open(fpath, "w", encoding="utf-8", buffering=_IO_BUF) as ff,
            open(kpath, "a", encoding="utf-8", buffering=_IO_BUF) as kf,
        ):
            mbatch: list[str] = []
            fbatch: list[str] = []
            kbatch: list[str] = []

            def _flush() -> None:
                if mbatch:
                    mf.write("\n".join(mbatch) + "\n")
                    ff.write("\n".join(fbatch) + "\n")
                    kf.write("\n".join(kbatch) + "\n")
                    mbatch.clear()
                    fbatch.clear()
                    kbatch.clear()

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
                            mbatch.append(clean)
                            fbatch.append(clean)
                            kbatch.append(key)
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

    except OSError as exc:
        logger.error("Output write failed: %s", exc)
        read_errors.append(str(exc))

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
    return dom == query or dom.endswith("." + query)


def extract_by_domain(
    query: str,
    mode: str = "combo",
    source_file: str | None = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> tuple[str, int]:
    """
    Extract all lines whose email domain matches query.
    query examples: "de", ".de", "@gmail.de", "gmail.com", ".net"
    Returns (output_filepath, count) or ("", 0).
    """
    query = query.strip().lower().lstrip("@").lstrip(".")
    if not query:
        return "", 0

    src = source_file or master_path(mode)
    if not os.path.exists(src):
        return "", 0

    parse_fn = _smtp_full if mode == "smtp" else _combo_full

    d     = os.path.join(_base(), "output")
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out   = os.path.join(d, f"extract_{query}_{stamp}.txt")

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
                r = parse_fn(line)
                if r and _domain_matches(r[2], query):
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
