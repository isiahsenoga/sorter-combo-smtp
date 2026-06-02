#!/usr/bin/env python3
"""
Deduplication Overview & Statistics Tool
Shows how deduplication works and provides statistics
"""

import os
from processor import master_path, _keys_path

print("=" * 90)
print("DEDUPLICATION SYSTEM - OVERVIEW & STATISTICS")
print("=" * 90)

# Check what files exist
combo_master = master_path("combo")
combo_keys = _keys_path("combo")
smtp_master = master_path("smtp")
smtp_keys = _keys_path("smtp")

print("\n[1] DEDUPLICATION SYSTEM OVERVIEW")
print("-" * 90)
print("""
How Deduplication Works:
───────────────────────

1. MASTER FILE (master.txt)
   └─ Stores all unique entries: "canonical form"
   └─ Example: "user@gmail.com:password" (COMBO)
   └─ Example: "host:port:user@gmail.com:password" (SMTP)

2. KEYS INDEX (master_keys.txt)
   └─ Fast lookup file
   └─ Stores only the dedup keys: "lowercase_email"
   └─ Used to check if entry already exists
   └─ Parallel loaded for speed (>10MB uses parallel loading)

3. SEEN_RUN SET
   └─ Tracks duplicates within CURRENT batch processing
   └─ Cleared after each batch
   └─ Prevents duplicate entries in fresh output

4. DEDUP LOGIC (in memory):
   ├─ Parse entry to extract key
   ├─ Check if key in existing_keys (from master) → SKIP if found
   ├─ Check if key in seen_run (from current batch) → SKIP if found
   └─ If new → ADD to master + fresh output + keys index

Result:
───────
✓ Zero duplicates in master.txt
✓ All entries unique by key
✓ Fast O(1) lookup via set
✓ Automatic cleanup on import
""")

print("\n[2] CURRENT STORAGE STATUS")
print("-" * 90)

def get_size(path):
    try:
        size = os.path.getsize(path)
        if size > 1024*1024:
            return f"{size / (1024*1024):.1f} MB"
        elif size > 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size} bytes"
    except OSError:
        return "NOT FOUND"

def count_lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0

print("COMBO Mode:")
print(f"  Master file:        {combo_master}")
print(f"  Size:               {get_size(combo_master)}")
print(f"  Entries:            {count_lines(combo_master):,}")
print(f"  Keys index:         {combo_keys}")
print(f"  Size:               {get_size(combo_keys)}")

print("\nSMTP Mode:")
print(f"  Master file:        {smtp_master}")
print(f"  Size:               {get_size(smtp_master)}")
print(f"  Entries:            {count_lines(smtp_master):,}")
print(f"  Keys index:         {smtp_keys}")
print(f"  Size:               {get_size(smtp_keys)}")

print("\n[3] DEDUPLICATION STATISTICS")
print("-" * 90)

combo_entries = count_lines(combo_master)
smtp_entries = count_lines(smtp_master)
combo_size = os.path.getsize(combo_master) if os.path.exists(combo_master) else 0
smtp_size = os.path.getsize(smtp_master) if os.path.exists(smtp_master) else 0

combo_keys_size = os.path.getsize(combo_keys) if os.path.exists(combo_keys) else 0
smtp_keys_size = os.path.getsize(smtp_keys) if os.path.exists(smtp_keys) else 0
total_keys_size = (combo_keys_size + smtp_keys_size) / (1024*1024)

print(f"""
Total Unique Entries:
  ├─ COMBO mode:    {combo_entries:,} entries
  ├─ SMTP mode:     {smtp_entries:,} entries
  └─ Total:         {combo_entries + smtp_entries:,} unique entries

Storage Efficiency:
  ├─ COMBO:         {combo_size / (1024*1024):.1f} MB ({combo_size / max(combo_entries, 1):.0f} bytes/entry avg)
  ├─ SMTP:          {smtp_size / (1024*1024):.1f} MB ({smtp_size / max(smtp_entries, 1):.0f} bytes/entry avg)
  └─ Total:         {(combo_size + smtp_size) / (1024*1024):.1f} MB

Dedup Key Efficiency:
  ├─ Keys storage:  {total_keys_size:.1f} MB (much smaller!)
  ├─ Lookup speed:  O(1) - constant time
  └─ Load time:     Parallel (multi-core) for >10MB files
""")

print("\n[4] DEDUPLICATION WORKFLOW")
print("-" * 90)
print("""
Processing a batch of SMTP entries:
──────────────────────────────────

┌─ Load Master Keys (fast-path)
│  ├─ Read smtp_keys.txt
│  ├─ Build set of ~300K existing keys
│  └─ Time: 0.5-5 seconds depending on size
│
├─ Process Input Files
│  ├─ For each entry:
│  │  ├─ Parse: "mail.example.com:587:user@gmail.com:pass"
│  │  ├─ Extract key: "user@gmail.com:mail.example.com"
│  │  │
│  │  ├─ Check 1: Is in existing_keys? → ALREADY IN MASTER
│  │  ├─ Check 2: Is in seen_run? → DUPLICATE IN BATCH
│  │  └─ Check 3: New entry? → ADD TO MASTER + FRESH + KEYS
│  │
│  ├─ Track statistics:
│  │  ├─ total: all lines processed
│  │  ├─ new_written: unique new entries
│  │  ├─ already_in: found in master
│  │  └─ dup_skip: duplicates within batch
│  │
│  └─ Batch write (every 20K entries)
│     ├─ Write to master.txt (append)
│     ├─ Write to fresh_*.txt (new entries only)
│     └─ Write to master_keys.txt (keys only)
│
└─ Report Results
   ├─ Total entries scanned
   ├─ New entries added
   ├─ Duplicates detected & skipped
   ├─ Domains found
   └─ Processing time & speed
""")

print("\n[5] DEDUPLICATION EXAMPLES")
print("-" * 90)
print("""
Example 1: Importing 1000 entries with 30% duplicates
─────────────────────────────────────────────────────

Input batch:  1000 lines
              ├─ 100 invalid (format check)
              ├─ 200 duplicates in batch
              ├─ 300 already in master
              └─ 400 NEW UNIQUE entries

Result:
  ├─ Total scanned:      1000 lines
  ├─ Format invalid:     100 lines (rejected early)
  ├─ Already in master:  300 lines (skipped)
  ├─ Duplicates:         200 lines (skipped)
  └─ WRITTEN:            400 NEW entries

Dedup statistics:
  ├─ Cleanup rate:       600/1000 = 60%
  ├─ Actual new data:    40%
  └─ Database growth:    +400 entries


Example 2: Processing the same 1000 entries again
──────────────────────────────────────────────────

Input batch:  1000 lines (SAME AS BEFORE)
              ├─ 100 invalid (format check)
              └─ 900 duplicates (all in master now!)

Result:
  ├─ Total scanned:      1000 lines
  ├─ Format invalid:     100 lines (rejected)
  ├─ Already in master:  900 lines (ALL skipped!)
  └─ WRITTEN:            0 NEW entries

Key insight:
  ├─ Second run: 100% dedup success!
  ├─ Database unchanged
  ├─ Processing time: Fast (O(1) lookups)
  └─ Storage unchanged
""")

print("\n[6] DEDUPLICATION PERFORMANCE")
print("-" * 90)
print("""
Speed of Dedup Operations:
──────────────────────────

Lookup speed:           O(1) - constant time (set-based)
Per-entry dedup check:  ~0.1-1 microsecond
Batch of 1M entries:    ~0.1-1 second for dedup checks

Key loading:
  ├─ Files < 10MB:      Sequential (linear speed)
  ├─ Files > 10MB:      Parallel (CPU-core count workers)
  ├─ Typical load time:  0.5-5 seconds for 100K-1M keys
  └─ Result: O(1) lookup set ready in seconds

Memory usage:
  ├─ Per key in memory:  ~24-50 bytes (Python string)
  ├─ 1M keys:            ~24-50 MB RAM
  ├─ 10M keys:           ~240-500 MB RAM
  └─ Scales linearly but manageable
""")

print("\n[7] HOW TO USE DEDUPLICATION")
print("-" * 90)
print("""
In your application (GUI/CLI):
──────────────────────────────

1. Click "Process Dataset" button
2. Select folder containing SMTP/COMBO files
3. System automatically:
   ├─ Loads existing master + keys
   ├─ Scans all files
   ├─ Deduplicates
   ├─ Outputs fresh_*.txt (only new entries)
   ├─ Updates master.txt (all unique entries)
   └─ Updates master_keys.txt (for next run)

Results shown:
  ├─ Total entries scanned
  ├─ New entries found
  ├─ Duplicates removed
  ├─ Domains extracted
  └─ Processing time

From Python:
────────────

from processor import process_dataset

result = process_dataset(
    folder="/path/to/files",
    settings={
        "mode": "smtp",
        "keyword_filter": True,
        "format_check": True,
    }
)

print(f"New entries: {result['new_written']}")
print(f"Duplicates skipped: {result['duplicates_skipped']}")
print(f"Already in master: {result['already_in_master']}")
""")

print("\n[8] TIPS FOR EFFECTIVE DEDUPLICATION")
print("-" * 90)
print("""
✓ ALWAYS USE MASTER FILE
  └─ Keeps single source of truth
  └─ Prevents unlimited growth of duplicates

✓ VALIDATE FORMAT FIRST
  └─ Invalid entries are skipped early
  └─ No wasted dedup checks on bad data

✓ USE FRESH OUTPUT FILES
  └─ fresh_*.txt contains ONLY new entries
  └─ Perfect for further processing
  └─ Ignore if you only want the master

✓ MONITOR FRESH OUTPUT SIZE
  └─ Decreasing fresh output = stable database
  └─ Increasing fresh output = new data found
  └─ Zero fresh output = all duplicates (good sign!)

✓ BACKUP BEFORE BULK OPERATIONS
  └─ master.txt is your database
  └─ master_keys.txt is the index
  └─ Both can be regenerated from master.txt if needed

✓ USE KEYWORD FILTER
  └─ Skips obviously unrelated files
  └─ Speeds up processing
  └─ Reduces noise
""")

print("\n" + "=" * 90)
print("✓ Deduplication is automatic and optimized for speed!")
print("=" * 90)
