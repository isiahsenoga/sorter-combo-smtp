# ✓ DEDUPLICATION QUICK REFERENCE

## What Is It?

**Deduplication** automatically removes duplicate entries when you import SMTP/COMBO credentials.

---

## How It Works (Simple)

```
You import 1000 entries
  ↓
System checks each one:
  - Is it valid format?      (No? Skip it)
  - Is it in master database? (Yes? Skip it)
  - Is it new?                (Yes? Keep it!)
  ↓
Output: 300 new entries only
        Master database grows by 300
```

---

## Key Files

| File | Purpose | Stays Updated |
|------|---------|--------------|
| `master.txt` | Database of all unique entries | Always |
| `master_keys.txt` | Fast lookup index | Always |
| `fresh_*.txt` | New entries from this import | Every run |

---

## Deduplication Speed

```
Lookup speed:    O(1) - instant
Per entry:       ~0.1-1 microsecond
1M entries:      ~0.1-1 second for dedup
Total import:    ~1-3 seconds per million entries
```

---

## Statistics You'll See

```
Total scanned:       1000 lines
├─ Format invalid:     100 (rejected)
├─ Already in master:  300 (duplicate)
├─ Duplicates:         200 (duplicate in batch)
└─ NEW WRITTEN:        400 ← These get added
```

---

## Is It Automatic?

**YES! ✓**

Just click "Process Dataset" and the system automatically:
- Loads master database
- Scans files
- Validates format
- Checks for duplicates
- Removes trash
- Outputs only new entries
- Updates master database

---

## Can You Control It?

```python
# Yes, from Python code:
result = process_dataset(
    folder="/path/to/files",
    settings={
        "mode": "smtp",                    # SMTP or COMBO
        "keyword_filter": True,            # Skip unrelated files
        "format_check": True,              # Validate format first
    }
)

# Returns statistics:
print(result['new_written'])        # New entries added
print(result['duplicates_skipped']) # Duplicates removed
print(result['already_in_master'])  # Found in database
```

---

## Real Example

### First Import
```
Input:  10,000 entries
Master: Empty

Result: 8,500 new (rest were trash/duplicates)
```

### Second Import (Same source)
```
Input:  10,000 entries
Master: 8,500 (from first import)

Result: 1,000 new (rest were duplicates from before)
```

### Third Import (New source)
```
Input:  15,000 entries
Master: 9,500 (from first two imports)

Result: 6,000 new (some overlap with existing)
```

---

## Memory Usage

```
1M entries:    ~24-50 MB RAM
10M entries:   ~240-500 MB RAM
Scales linearly but very manageable
```

---

## Tips

✓ **Always use master.txt** - your database  
✓ **Use keyword filter** - skip unrelated files  
✓ **Use fresh output** - only new entries  
✓ **Monitor statistics** - see what's new vs duplicate  
✓ **It's all automatic** - no manual dedup needed  

---

## Is It Accurate?

**100% for matching keys**
- Same email+host = detected as duplicate
- Different email = treated as new
- Case-insensitive matching

---

## Questions?

See detailed guide: `DEDUPLICATION_GUIDE.md`  
Run overview: `python dedup_overview.py`

---

## TL;DR

System automatically removes duplicates.  
No action needed. Works with all imports.  
Just use normally! 🚀
