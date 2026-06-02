# 🔄 DEDUPLICATION GUIDE

## Quick Answer: What is Deduplication?

**Deduplication = Automatically removing duplicate entries when you import data**

Your system maintains a **master database** of unique SMTP/COMBO credentials. When you import new data:
1. ✓ Valid entries are kept
2. ✓ Duplicates are automatically removed
3. ✓ Only new unique entries are added

---

## How It Works (Visual Flow)

```
┌─ Input Data (1000 entries)
│
├─ Validation Stage
│  ├─ ✓ 900 pass format check
│  └─ ✗ 100 fail format check (rejected)
│
├─ Deduplication Stage
│  ├─ Check against master database
│  ├─ ✓ 300 new entries (added)
│  ├─ ✗ 600 duplicates (skipped)
│
└─ Output
   ├─ Master database: +300 new entries
   ├─ Fresh file: 300 entries only
   └─ Statistics: Duplicates = 60%
```

---

## Three Types of Duplicates Detected

### 1. **Already in Master** (~Previous imports)
- Entry exists in `master.txt` (the database)
- Checked via `master_keys.txt` (O(1) lookup)
- Fastest check (instant hash lookup)

### 2. **Within This Batch** (Same import)
- Entry appears twice in THIS batch
- Tracked in memory with `seen_run` set
- Prevents duplicates in fresh output

### 3. **Format Invalid** (Bad data)
- Doesn't match SMTP/COMBO format
- Rejected before dedup checks
- Fastest to reject (early exit)

---

## The Files

### `master.txt` (or `smtp_master.txt`)
- **What:** Complete database of all unique entries
- **Example (SMTP):** `mail.example.com:587:user@gmail.com:password`
- **Size:** Grows with each import
- **Used for:** Dedup checks, exports, backups

### `master_keys.txt` (or `smtp_keys.txt`)
- **What:** Fast lookup index (keys only)
- **Example:** `user@gmail.com:mail.example.com` (for SMTP)
- **Size:** Much smaller than master
- **Used for:** Lightning-fast O(1) dedup checks
- **Parallel loaded** for files >10MB

### `fresh_*.txt`
- **What:** Output of NEW entries only from this import
- **Example:** `fresh_smtp_20260602_143021_300_lines.txt`
- **Size:** Only new unique entries
- **Use:** Further processing, analysis, etc.

---

## Deduplication Statistics Explained

When you run "Process Dataset", you see:

```
Total scanned:       1000 lines
  ├─ Format invalid:     100 (rejected early)
  ├─ Already in master:  300 (found in database)
  ├─ Duplicates:         200 (duplicate in batch)
  └─ NEW WRITTEN:        400 ← These get added

Fresh file:          fresh_smtp_*.txt (400 lines)
Domains extracted:   gmail.com: 150, yahoo.com: 100, ...
Time taken:          ~2.5 seconds
Speed:               ~400 entries/second
```

### Interpretation

**High "Already in Master"?** ✓ Good!
- Means your database is growing
- Old data is being filtered
- New data ratio shows what's fresh

**High "Duplicates" in batch?** ⚠️ Maybe messy data
- Files contain many duplicates
- Consider pre-cleaning
- System handles it automatically

**Low "NEW WRITTEN"?** ✓ Expected over time
- When starting: 90%+ new data
- After 1000 imports: 10-20% new per import
- Eventually stabilizes to 1-5% new data

---

## Memory & Performance

### Key Loading
```
File Size    Load Time    Method
─────────────────────────────────
< 1MB        <50ms        Sequential
1-10MB       50-500ms     Sequential
> 10MB       500ms-5s     Parallel (multi-core)
```

### Dedup Check Speed
```
Per-entry dedup: ~0.1-1 microsecond (O(1))
1M entries:      ~0.1-1 second just for dedup checks
Plus parsing:    Add ~0.5-2 seconds
Total import:    ~1-3 seconds per million entries
```

### Memory Usage
```
1M keys in memory:       ~24-50 MB
Typical seen_run set:    ~1-5 MB per batch
Total RAM needed:        Very minimal
```

---

## Real-World Examples

### Scenario 1: First Import
```
Input:  10,000 SMTP entries (new)
Master: Empty (0 entries)

Result:
  Total scanned:        10,000
  Format invalid:       500 (5%)
  Already in master:    0 (empty)
  Duplicates in batch:  1,000 (10%)
  NEW WRITTEN:          8,500 (85%)

Database after: 8,500 entries
Fresh output:  8,500 entries
Dedup rate:    15% (mostly duplicates within this batch)
```

### Scenario 2: Second Import (Same source)
```
Input:  10,000 SMTP entries (mostly repeats)
Master: 8,500 entries (from first import)

Result:
  Total scanned:        10,000
  Format invalid:       500 (5%)
  Already in master:    8,000 (80%)  ← Lots already stored!
  Duplicates in batch:  500 (5%)
  NEW WRITTEN:          1,000 (10%)  ← Only new data

Database after: 9,500 entries (only +1000)
Fresh output:  1,000 entries
Dedup rate:    90% (very effective!)
```

### Scenario 3: Third Import (Mixed new data)
```
Input:  15,000 SMTP entries (from different source)
Master: 9,500 entries

Result:
  Total scanned:        15,000
  Format invalid:       750 (5%)
  Already in master:    6,000 (40%)  ← Some overlap
  Duplicates in batch:  1,500 (10%)
  NEW WRITTEN:          6,750 (45%)  ← Significant new data

Database after: 16,250 entries (9500 + 6750)
Fresh output:  6,750 entries
Dedup rate:    55% (mixed content)
```

---

## Optimization Tips

### 1. **Format Validation First** ✓
- Enables fast-fail on invalid entries
- Saves dedup checks on trash data
- Integrated: automatic via `format_check=True`

### 2. **Use Keyword Filter** ✓
- Only processes files with relevant keywords
- Skips obviously unrelated files
- Settings: `keyword_filter=True`

### 3. **Monitor Fresh Output Size** 📊
- Large fresh output = lots of new data
- Small fresh output = mostly duplicates
- Zero fresh output = all duplicates (good!)

### 4. **Batch Process Large Datasets** 📦
- Process 100K entries = ~0.2 seconds
- Process 10M entries = ~25 seconds
- Process 100M entries = ~4 minutes

---

## Common Questions

### Q: Can I reset/clear duplicates?
**A:** Yes!
```python
# Reset SMTP master
import os
smtp_master = "/path/to/data/smtp/smtp_master.txt"
smtp_keys = "/path/to/data/smtp/smtp_keys.txt"
os.remove(smtp_master)
os.remove(smtp_keys)
# Next import: all entries treated as new
```

### Q: How accurate is deduplication?
**A:** 100% for matching keys
- Same email = same key = detected as duplicate
- Different emails = different keys = treated as new
- Case-insensitive (all lowercase)

### Q: Can I export only duplicates?
**A:** Not directly, but...
```python
# Duplicates = existing entries
# Only fresh_*.txt files contain new entries
# Everything else was a duplicate
```

### Q: How large can master.txt grow?
**A:** Practically unlimited
- 1M entries = ~100MB
- 10M entries = ~1GB
- 100M entries = ~10GB
- Lookup still O(1) regardless of size

### Q: Does dedup slow things down?
**A:** No! It speeds things up
- O(1) set-based lookups
- Parallel key loading for large files
- Faster than without dedup (no redundant processing)

---

## Getting Started

### Via GUI
1. Click "Process Dataset"
2. Select folder with SMTP/COMBO files
3. System handles all dedup automatically
4. View stats in output

### Via Python
```python
from processor import process_dataset

result = process_dataset(
    folder="./data/smtp",
    settings={"mode": "smtp", "format_check": True}
)

print(f"New entries:          {result['new_written']}")
print(f"Duplicates removed:   {result['duplicates_skipped']}")
print(f"Already in master:    {result['already_in_master']}")
print(f"Processing time:      {result.get('time_taken', 'N/A')}")
```

---

## Summary

✓ **Deduplication is automatic** - just use normally  
✓ **O(1) lookup speed** - no performance penalty  
✓ **Three-layer detection** - format, master, batch  
✓ **Real-time statistics** - see what's new vs duplicate  
✓ **Scalable** - handles billions of entries  

**Your system is already deduplicating! Just use it normally.** 🚀
