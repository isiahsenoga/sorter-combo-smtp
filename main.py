from __future__ import annotations
import logging
import os
import sys


def _ensure_deps() -> None:
    try:
        import tqdm  # noqa: F401
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "tqdm"])


def _yn(prompt: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    ans  = input(f"{prompt} ({hint}): ").strip().lower()
    return (ans != "n") if default_yes else (ans == "y")


def main() -> None:
    _ensure_deps()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    from tqdm import tqdm
    from processor import (
        process_dataset, delete_scanned_files,
        extract_by_domain, master_path, reports_dir,
    )
    from analytics import show_domain_stats, export_report
    from scanner import DEFAULT_KEYWORDS

    print("Dataset Toolkit")
    print("=" * 60)

    # ── mode ─────────────────────────────────────────────────────────────────
    print("\n  [1] Combo scanner   (email:pass -> data/master.txt)")
    print("  [2] SMTP merger     (host:port:user:pass -> data/smtp_master.txt)")
    print("  [3] Combo + SMTP merge")
    print("  [4] Domain extract  (pull .de / .net / gmail.com from master)")
    choice = input("\nChoice [1]: ").strip() or "1"

    # ── domain extract ────────────────────────────────────────────────────────
    if choice == "4":
        mode = input("Source mode — combo or smtp [combo]: ").strip().lower() or "combo"
        print(f"\nMaster: {master_path(mode)}")
        query = input("Domain / TLD to extract (e.g.  de  or  gmail.com): ").strip()
        if not query:
            print("No query entered.")
            sys.exit(0)

        pbar = tqdm(total=0, unit="lines", desc="Extracting", file=sys.stdout)

        def on_ext_progress(done: int, total: int) -> None:
            if pbar.total != total:
                pbar.total = total
                pbar.refresh()
            pbar.n = done
            pbar.refresh()

        out_path, count = extract_by_domain(query, mode=mode, progress_cb=on_ext_progress)
        pbar.close()

        if out_path:
            print(f"\nExtracted {count:,} entries  →  {out_path}")
        else:
            print(f"\nNo entries found for '{query}'.")
        sys.exit(0)

    # ── scan mode ─────────────────────────────────────────────────────────────
    if choice == "3":
        mode = "both"
        print(f"\nCombo master : {master_path('combo')}")
        print(f"SMTP master  : {master_path('smtp')}")
    else:
        mode = "smtp" if choice == "2" else "combo"
        print(f"\nMaster file: {master_path(mode)}")
    print()

    folder = input("Folder to scan: ").strip().strip('"').strip("'")
    if not folder or not os.path.isdir(folder):
        print(f"ERROR: not a valid directory: {folder!r}")
        sys.exit(1)

    kw_filter = _yn("Filter by filename keywords?", default_yes=False)
    keywords  = DEFAULT_KEYWORDS
    if kw_filter:
        custom = input(f"  Keywords (comma-separated) [{','.join(DEFAULT_KEYWORDS)}]: ").strip()
        if custom:
            keywords = [k.strip() for k in custom.split(",") if k.strip()]

    if mode == "smtp":
        fmt_check = _yn("Skip files with <10% SMTP credential lines (format check)?", default_yes=True)
    else:
        fmt_check = _yn("Skip files with <10% email:pass lines (format check)?", default_yes=True)

    settings = {
        "mode":           mode,
        "keyword_filter": kw_filter,
        "keywords":       keywords,
        "format_check":   fmt_check,
    }

    from scanner import scan_files, is_combo_file, is_smtp_file
    candidate_files = list(scan_files(folder, keyword_filter=kw_filter, keywords=keywords))
    if not candidate_files:
        print("No matching files found.")
        sys.exit(0)

    if mode == "both":
        combo_files = candidate_files
        smtp_files = candidate_files
        if fmt_check:
            combo_files = [f for f in candidate_files if is_combo_file(f)]
            smtp_files = [f for f in candidate_files if is_smtp_file(f)]
        if not combo_files and not smtp_files:
            print("No matching files passed the format check for either mode.")
            sys.exit(0)

        if combo_files:
            print(f"\nFound {len(combo_files)} combo file(s). Scanning combo...\n")
            pbar_combo = tqdm(total=len(combo_files), unit="file", desc="Combo", file=sys.stdout)

            def on_progress_combo(done: int, total: int, filename: str) -> None:
                pbar_combo.set_postfix_str(filename[:40], refresh=False)
                pbar_combo.n = done
                pbar_combo.refresh()

            combo_settings = settings.copy()
            combo_settings["mode"] = "combo"
            stats_combo = process_dataset(folder, settings=combo_settings, progress_cb=on_progress_combo)
            pbar_combo.close()
            show_domain_stats(stats_combo)
            combo_fresh = stats_combo["fresh_file"]
            print(f"\n  [Combo RESULTS]")
            if combo_fresh:
                print(f"  Fresh file: {os.path.basename(combo_fresh)}")
            else:
                print("  No new combo entries — fresh file not created.")
            rpt_combo = os.path.join(reports_dir(), "combo")
            export_report(stats_combo["domains"], stats_combo["new_written"], rpt_combo)
            print(f"  Combo Reports: {rpt_combo}")
        else:
            print("\nNo combo files found for processing.")

        if smtp_files:
            print(f"\nFound {len(smtp_files)} smtp file(s). Scanning smtp...\n")
            pbar_smtp = tqdm(total=len(smtp_files), unit="file", desc="SMTP", file=sys.stdout)

            def on_progress_smtp(done: int, total: int, filename: str) -> None:
                pbar_smtp.set_postfix_str(filename[:40], refresh=False)
                pbar_smtp.n = done
                pbar_smtp.refresh()

            smtp_settings = settings.copy()
            smtp_settings["mode"] = "smtp"
            stats_smtp = process_dataset(folder, settings=smtp_settings, progress_cb=on_progress_smtp)
            pbar_smtp.close()
            show_domain_stats(stats_smtp)
            smtp_fresh = stats_smtp["fresh_file"]
            print(f"\n  [SMTP RESULTS]")
            if smtp_fresh:
                print(f"  Fresh file: {os.path.basename(smtp_fresh)}")
            else:
                print("  No new smtp entries — fresh file not created.")
            rpt_smtp = os.path.join(reports_dir(), "smtp")
            export_report(stats_smtp["domains"], stats_smtp["new_written"], rpt_smtp)
            print(f"  SMTP Reports: {rpt_smtp}")
        else:
            print("\nNo smtp files found for processing.")

        print(f"\n  Mode: CLI (Combo + SMTP merge)")
        sys.exit(0)

    if fmt_check:
        valid_files = []
        for f in candidate_files:
            if mode == "smtp":
                valid = is_smtp_file(f)
            else:
                valid = is_combo_file(f)
            if valid:
                valid_files.append(f)
        candidate_files = valid_files

    if not candidate_files:
        print("No matching files passed the format check.")
        sys.exit(0)

    print(f"\nFound {len(candidate_files)} file(s). Scanning...\n")

    pbar = tqdm(total=len(candidate_files), unit="file", desc="Reading", file=sys.stdout)

    def on_progress(done: int, total: int, filename: str) -> None:
        pbar.set_postfix_str(filename[:40], refresh=False)
        pbar.n = done
        pbar.refresh()

    stats = process_dataset(folder, settings=settings, progress_cb=on_progress)
    pbar.close()

    # ── results ───────────────────────────────────────────────────────────────
    show_domain_stats(stats)

    fresh = stats["fresh_file"]
    print(f"\n  [RESULTS]")
    if fresh:
        fname = os.path.basename(fresh)
        print(f"  Fresh file: {fname}")
    else:
        print(f"  No new entries — fresh file not created.")
    print(f"  Mode: {'CLI (Combo Scanner)' if mode == 'combo' else 'CLI (SMTP Merger)'}")

    rpt = reports_dir()
    export_report(stats["domains"], stats["new_written"], rpt)
    print(f"\n  Master: {master_path(mode)}")
    print(f"  Reports: {rpt}")

    # ── delete prompt ─────────────────────────────────────────────────────────
    if stats["scanned_files"]:
        print()
        if _yn("Delete scanned input files?", default_yes=False):
            deleted, errs = delete_scanned_files(stats["scanned_files"])
            print(f"Deleted {deleted} file(s)." + (f"  ({errs} errors)" if errs else ""))
        else:
            print("Input files kept.")


if __name__ == "__main__":
    main()
