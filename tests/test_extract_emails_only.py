#!/usr/bin/env python3
"""Basic tests for email-only extraction output."""

import os
import tempfile

from processor import extract_by_domain, extract_by_email

DATA = """
smtp.strato.de:587:alice@strato.de:Pass123
smtp.ionos.com:465:bob@ionos.de:Secret456
smtp.mail.com:25:carol@mail.com:Test789
""".strip()

COMBO_DATA = """
alice@strato.de:Pass123
bob@ionos.de:Secret456
carol@mail.com:Test789
""".strip()


def _read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test_smtp.txt")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(DATA + "\n")

        out_path, count = extract_by_email("strato", mode="smtp", source_file=src, emails_only=True)
        assert out_path and count == 1, "Expected one strato email"
        assert _read_lines(out_path) == ["alice@strato.de"]

        out_path, count = extract_by_domain("ionos", mode="smtp", source_file=src, emails_only=True)
        assert out_path and count == 1, "Expected one ionos email"
        assert _read_lines(out_path) == ["bob@ionos.de"]

        out_path, count = extract_by_email("@mail.com", mode="smtp", source_file=src, emails_only=True)
        assert out_path and count == 1, "Expected one mail.com email"
        assert _read_lines(out_path) == ["carol@mail.com"]

        combo_src = os.path.join(tmpdir, "test_combo.txt")
        with open(combo_src, "w", encoding="utf-8") as fh:
            fh.write(COMBO_DATA + "\n")

        out_path, count = extract_by_email("strato", mode="combo", source_file=combo_src, emails_only=True)
        assert out_path and count == 1, "Expected one strato combo email"
        assert _read_lines(out_path) == ["alice@strato.de"]

        out_path, count = extract_by_domain("ionos", mode="combo", source_file=combo_src, emails_only=True)
        assert out_path and count == 1, "Expected one ionos combo email"
        assert _read_lines(out_path) == ["bob@ionos.de"]

        out_path, count = extract_by_email("@mail.com", mode="combo", source_file=combo_src, emails_only=True)
        assert out_path and count == 1, "Expected one mail.com combo email"
        assert _read_lines(out_path) == ["carol@mail.com"]

    print("✓ test_extract_emails_only passed")


if __name__ == "__main__":
    main()
