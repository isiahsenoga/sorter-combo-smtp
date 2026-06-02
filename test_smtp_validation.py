#!/usr/bin/env python3
"""
Test script to validate the improved SMTP parsing and rejection logic.
Tests against the user-provided trash data examples.
"""

from processor import parse_smtp, _smtp_full, canonicalize_smtp

# Bad entries that should be REJECTED
BAD_ENTRIES = [
    "hsbc::eunbiekang@gmail.com:Eun",              # Empty port (::)
    "hsbc::lauren_509@hotmail.com:Lauren",         # Empty port
    "hsbc::krissy.aus@gmail.com:Kristabelle",      # Empty port
    "user::email@test.com:",                        # Empty password
    "::email@test.com:pass",                        # Empty host, empty port
    "host::email@test.com:x",                       # Empty port, single-char password
]

# Good entries that should be ACCEPTED (examples from user data)
GOOD_ENTRIES = [
    "casey:5189:mugzilla25@msn.com:mugzilla25@msn.com",
    "robert:64264:nickelbk77@gmail.com:Other",
    "roger:12269:rwheatbrook@yahoo.com:rwheatbrook@yahoo.com",
    "joy:3437:joylshreve@gmail.com:joylshreve@gmail.com",
    "mail.example.com:587:user@gmail.com:MyPassword123",
    "smtp.gmail.com:465:admin@company.com:SecurePass456",
]

print("=" * 80)
print("SMTP VALIDATION TEST")
print("=" * 80)

print("\n[TESTING BAD ENTRIES - Should all return None]")
print("-" * 80)
rejected_count = 0
for entry in BAD_ENTRIES:
    result = _smtp_full(entry)
    status = "✓ REJECTED" if result is None else "✗ ACCEPTED (BAD!)"
    print(f"{status:20} | {entry}")
    if result is None:
        rejected_count += 1
    else:
        print(f"                     └─ Parsed as: {result[0]}")

print(f"\nBad entries rejected: {rejected_count}/{len(BAD_ENTRIES)}")

print("\n[TESTING GOOD ENTRIES - Should all parse successfully]")
print("-" * 80)
accepted_count = 0
for entry in GOOD_ENTRIES:
    result = _smtp_full(entry)
    if result:
        canonical, key, domain = result
        status = "✓ ACCEPTED"
        print(f"{status:20} | {entry}")
        print(f"                     └─ Canonical: {canonical}")
        print(f"                     └─ Key: {key}")
        print(f"                     └─ Domain: {domain}")
        accepted_count += 1
    else:
        status = "✗ REJECTED (BAD!)"
        print(f"{status:20} | {entry}")

print(f"\nGood entries accepted: {accepted_count}/{len(GOOD_ENTRIES)}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"Bad entries correctly rejected: {rejected_count}/{len(BAD_ENTRIES)}")
print(f"Good entries correctly accepted: {accepted_count}/{len(GOOD_ENTRIES)}")

if rejected_count == len(BAD_ENTRIES) and accepted_count == len(GOOD_ENTRIES):
    print("\n✓ ALL TESTS PASSED!")
else:
    print("\n✗ SOME TESTS FAILED")
