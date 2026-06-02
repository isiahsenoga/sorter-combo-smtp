#!/usr/bin/env python3
"""
Extended edge-case tests for SMTP validation.
Tests various malformed and borderline entries.
"""

from processor import _smtp_full

# Edge cases: should be rejected
EDGE_CASES_REJECT = [
    ("", "Empty string"),
    ("   ", "Whitespace only"),
    ("email@test.com", "Missing host/port/password"),
    ("host|587|email@test.com|pass", "Using pipe separator - no email in parts"),
    ("x:5189:test@test.com:pass", "Single-char hostname"),
    ("host:x:test@test.com:pass", "Non-numeric port"),
    ("host:99999:test@test.com:pass", "Port out of range (>65535)"),
    ("host:0:test@test.com:pass", "Port out of range (0)"),
    ("host:5189:test@test.com:", "Empty password"),
    ("host:5189:test@test.com:x", "Single-char password"),
    ("host:5189:noemail:pass", "No @ symbol in email field"),
    ("host:5189:@test.com:pass", "Empty email user part"),
    ("host:5189:test@:pass", "Empty email domain"),
    ("host:5189:test@.com:pass", "Domain starts with dot"),
    ("host:5189:test@test:pass", "Domain without dot"),
    ("host:5189:test@test.c:none", "Password is 'none' (placeholder)"),
    ("host:5189:test@test.c:null", "Password is 'null' (placeholder)"),
    ("host:5189:test@test.c:password", "Password is 'password' (placeholder)"),
]

# Edge cases: should be accepted
EDGE_CASES_ACCEPT = [
    ("mail:25:admin@test.co.uk:P@ss123", "Subdomain with multiple dots"),
    ("smtp.example.org:587:user@company.io:MyP@ss", "Double subdomain"),
    ("mail-server:465:user@test-domain.com:SecurePass1", "Hyphenated hostname"),
    ("mail_host:2525:test@multi.domain.co.nz:Pass_123", "Underscore in hostname"),
    ("192.168.1.1:587:admin@domain.com:password123", "Space-separated (valid if parts parse)"),
]

print("=" * 100)
print("EXTENDED EDGE-CASE TESTS")
print("=" * 100)

print("\n[EDGE CASES - SHOULD REJECT]")
print("-" * 100)
reject_passed = 0
for entry, description in EDGE_CASES_REJECT:
    result = _smtp_full(entry)
    status = "✓" if result is None else "✗"
    print(f"{status} {description:45} | {entry[:50]}")
    if result is None:
        reject_passed += 1
    else:
        print(f"  └─ ERROR: Accepted when should reject: {result[0]}")

print(f"\nRejection tests passed: {reject_passed}/{len(EDGE_CASES_REJECT)}")

print("\n[EDGE CASES - SHOULD ACCEPT]")
print("-" * 100)
accept_passed = 0
for entry, description in EDGE_CASES_ACCEPT:
    result = _smtp_full(entry)
    status = "✓" if result else "✗"
    print(f"{status} {description:45} | {entry[:50]}")
    if result:
        accept_passed += 1
    else:
        print(f"  └─ ERROR: Rejected when should accept")

print(f"\nAcceptance tests passed: {accept_passed}/{len(EDGE_CASES_ACCEPT)}")

print("\n" + "=" * 100)
print("SUMMARY")
print("=" * 100)
total = len(EDGE_CASES_REJECT) + len(EDGE_CASES_ACCEPT)
passed = reject_passed + accept_passed
print(f"Passed: {passed}/{total} edge-case tests")

if passed == total:
    print("✓ ALL EDGE-CASE TESTS PASSED!")
else:
    print(f"✗ {total - passed} tests failed")
