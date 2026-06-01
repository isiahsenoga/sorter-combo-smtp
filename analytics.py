from __future__ import annotations
import csv
import logging
import os
from collections import Counter

logger = logging.getLogger(__name__)


def show_domain_stats(stats: dict) -> None:
    """Print a formatted summary of the stats dict returned by process_dataset."""
    domains: Counter = stats.get("domains", Counter())
    total    = stats.get("total_scanned", 0)
    written  = stats.get("new_written", 0)
    dup      = stats.get("duplicates_skipped", 0)
    existing = stats.get("already_in_master", 0)
    fmt_skip = stats.get("format_skipped", 0)
    errors   = stats.get("error_count", 0)
    mode     = stats.get("mode", "combo")

    print(f"\nResults  [{mode.upper()} mode]")
    print("=" * 50)
    print(f"  Lines scanned        : {total:,}")
    print(f"  New entries added    : {written:,}")
    print(f"  Dupes in this scan   : {dup:,}")
    print(f"  Already in master    : {existing:,}")
    if fmt_skip:
        print(f"  Format-skipped files : {fmt_skip}")
    print(f"  Errors               : {errors}")
    print(f"  Unique domains found : {len(domains):,}")

    if domains and written:
        print()
        print(f"  TOP DOMAINS ({len(domains):,} total)")
        print(f"  " + "-" * 60)
        for domain, count in domains.most_common(10):
            pct = count / written * 100
            bar = "#" * max(1, int(pct / 2.5))
            print(f"  {domain:<30} {count:>10,}  {pct:>6.2f}%  {bar}")
        if len(domains) > 10:
            print(f"  ... and {len(domains) - 10} more domains")


def export_report(domains: Counter, total: int, output_dir: str) -> None:
    """Write domain_report.txt and domain_report.csv into output_dir."""
    os.makedirs(output_dir, exist_ok=True)

    txt_path = os.path.join(output_dir, "domain_report.txt")
    csv_path = os.path.join(output_dir, "domain_report.csv")

    try:
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(f"Total records : {total}\n")
            fh.write(f"Unique domains: {len(domains)}\n\n")
            fh.write(f"{'Domain':<30} {'Count':>8}   {'%':>6}\n")
            fh.write("-" * 50 + "\n")
            for domain, count in domains.most_common():
                pct = (count / total * 100) if total else 0.0
                fh.write(f"{domain:<30} {count:>8,}   {pct:>5.2f}%\n")
    except OSError as exc:
        logger.error("Could not write %s: %s", txt_path, exc)

    try:
        with open(csv_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["domain", "count", "percent"])
            for domain, count in domains.most_common():
                pct = (count / total * 100) if total else 0.0
                writer.writerow([domain, count, f"{pct:.2f}"])
    except OSError as exc:
        logger.error("Could not write %s: %s", csv_path, exc)
