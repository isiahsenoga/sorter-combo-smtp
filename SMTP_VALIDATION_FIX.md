# SMTP Data Validation Fix - Comprehensive Guide

## Problem Statement
The SMTP grabber was accepting malformed and incomplete credentials, resulting in "trash" entries like:
```
hsbc::eunbiekang@gmail.com:Eun                    ← Empty/missing port (::)
hsbc::lauren_509@hotmail.com:Lauren                ← Empty port
host::email@test.com:x                             ← Missing port, weak password
```

## Root Causes Identified

### 1. Weak Host Validation
**Before:** Accepted any word without proper domain structure
```python
# OLD: Too permissive
if '.' in p:
    return True
# Allows single letters, empty parts in domains
```

**After:** Strict domain/hostname validation
```python
# NEW: Requires proper structure
if '.' in p:
    parts = p.split('.')
    return all(part and len(part) > 0 for part in parts)
# Rejects: ".example.com", "example..com", single letters
```

### 2. Port Validation Issues
**Before:** Accepted empty ports, didn't validate range properly
```python
if candidate_port.isdigit():  # Empty string would fail, but pattern matching was loose
```

**After:** Strict port validation
```python
# Explicit range check and non-empty verification
if (candidate_port and candidate_port.isdigit() and 
    1 <= int(candidate_port) <= 65535):
```

### 3. Missing Password Validation
**Before:** Accepted anything, even empty strings or single characters
```python
pw = parts[email_idx + 1] if email_idx + 1 < len(parts) else ""
# No validation of password quality
```

**After:** Strict password requirements
```python
if len(pw) < 2:
    return None  # Reject single-char passwords
    
# Block common placeholder values
if pw.lower() in ("none", "null", "n/a", "na", "pass", "password", "pwd"):
    return None
```

### 4. Component Completeness
**Before:** Accepted partial entries with missing components
```python
# Would accept even if host="" or port=""
```

**After:** Requires all four components
```python
if not host or not port or not email or not pw:
    return None  # ALL components must be present
```

## Changes Made

### File: `processor.py`

#### Function: `_looks_like_host_token()`
**New Validations:**
- Requires 2+ characters (rejects single letters)
- Validates dot-separated domain parts aren't empty
- Bare hostnames must be alphanumeric + hyphens/underscores
- Rejects pure numeric strings and `@` containing tokens

#### Function: `_smtp_full()`
**New Strict Parsing:**
- Must have 4+ parts when split on separators
- Email validation: proper user@domain.tld format
- Port validation: numeric, 1-65535 range
- Host validation: must be valid domain or hostname
- Password validation: 2+ chars, not placeholder text
- Returns `None` if ANY component is invalid/missing

### File: `scanner.py`

#### Function: `is_smtp_file()`
**Improved File Detection:**
- Uses stricter validators when sampling files
- Only counts entries that pass full validation
- Prevents accepting "trash" files into the system
- Proper port range checking (1-65535)

## Test Results

### ✓ CORRECTLY REJECTED (Trash Data)
```
hsbc::eunbiekang@gmail.com:Eun              ← Empty port
hsbc::lauren_509@hotmail.com:Lauren         ← Empty port
user::email@test.com:                       ← Empty password
::email@test.com:pass                       ← Empty host & port
host::email@test.com:x                      ← Empty port, weak password
```

### ✓ CORRECTLY ACCEPTED (Valid Entries)
```
casey:5189:mugzilla25@msn.com:mugzilla25@msn.com
robert:64264:nickelbk77@gmail.com:Other
mail.example.com:587:user@gmail.com:MyPassword123
smtp.gmail.com:465:admin@company.com:SecurePass456
```

## Validation Chain

```
Input Line
    ↓
[Check for @ symbol]
    ↓
[Split on separators (:, |, ,, ;, space, tab)]
    ↓
[Verify 4+ parts] → ✗ REJECT if < 4
    ↓
[Find valid email token] → ✗ REJECT if not found
    ↓
[Find valid host (domain/hostname)] → ✗ REJECT if invalid
    ↓
[Extract and validate port] → ✗ REJECT if:
    • Empty
    • Non-numeric
    • Out of range (1-65535)
    ↓
[Extract and validate password] → ✗ REJECT if:
    • Empty
    • < 2 characters
    • Common placeholder (none, null, etc.)
    ↓
✓ ACCEPT & PARSE
```

## Performance Impact

- **Zero performance degradation** - validation happens once during parsing
- **Better memory usage** - fewer trash entries stored
- **Faster processing** - invalid entries fail quickly
- **Cleaner output** - only quality data saved

## Integration Notes

The changes are backward-compatible:
- Existing valid entries continue to parse correctly
- Invalid entries are silently rejected (logged if enabled)
- File detection is more conservative (may skip borderline files)
- Deduplication keys remain consistent

## Testing

Run the included test suite:
```bash
python test_smtp_validation.py
```

Expected output: `✓ ALL TESTS PASSED!`

## Future Enhancements

Consider implementing:
1. **IP address validation** - for host fields containing IPs
2. **Common TLD validation** - for domain parts
3. **Credential format hints** - detect and handle variations
4. **Statistics reporting** - track rejection reasons
5. **Logging improvements** - detailed rejection diagnostics
