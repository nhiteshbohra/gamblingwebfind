#!/usr/bin/env python3
"""
pdf_domain_ip_extractor_fast.py — Extract "Target Domain" and "Resolved IP
(A Record)" pairs from large forensic/takedown-report PDFs (100s of MB,
1000s of pages) and write them to a CSV, FAST and with low memory use.

Why this is fast for big files (vs. the pdfplumber version):
    - Uses poppler's `pdftotext` (compiled C++) to extract text instead of
      pdfplumber's pure-Python page parser. This is typically 10-50x faster
      on large files, especially ones with embedded screenshot images (which
      pdfplumber still has to parse the object structure around, even
      though it doesn't render them).
    - Streams the extracted text page-by-page (splitting on the form-feed
      character pdftotext inserts between pages) instead of loading the
      whole document into memory at once.
    - Writes CSV rows incrementally as it goes, instead of collecting
      everything into a list first.

Requirements:
    - poppler-utils installed (provides `pdftotext`). On Ubuntu/Debian:
        sudo apt-get install poppler-utils
      On Mac:
        brew install poppler

Usage:
    python3 pdf_domain_ip_extractor_fast.py report.pdf
    python3 pdf_domain_ip_extractor_fast.py report.pdf -o results.csv
    python3 pdf_domain_ip_extractor_fast.py report.pdf --full-line
    python3 pdf_domain_ip_extractor_fast.py report.pdf --keep-text   # keep the intermediate .txt file
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

DOMAIN_RE = re.compile(r"Target Domain:\s*(\S+)")
IP_RE = re.compile(r"Resolved IP\s*\(A\s+([\d]{1,3}(?:\.[\d]{1,3}){3})")

FORM_FEED = "\x0c"


def check_pdftotext():
    if shutil.which("pdftotext") is None:
        print(
            "ERROR: 'pdftotext' not found. Install poppler-utils:\n"
            "  Ubuntu/Debian: sudo apt-get install poppler-utils\n"
            "  macOS:         brew install poppler\n",
            file=sys.stderr,
        )
        sys.exit(1)


def run_pdftotext(pdf_path: str, txt_path: str):
    """Convert PDF to plain text using poppler's pdftotext (fast, C++)."""
    subprocess.run(
        ["pdftotext", "-layout", pdf_path, txt_path],
        check=True,
    )


def process_page(page_num: int, text: str, full_line: bool):
    """Extract Target Domain / Resolved IP pairs from a single page's text."""
    domain_matches = list(DOMAIN_RE.finditer(text))
    if not domain_matches:
        return []

    ip_matches = list(IP_RE.finditer(text))

    # De-duplicate repeated "Target Domain:" occurrences with the same value
    seen = set()
    unique_domains = []
    for m in domain_matches:
        val = m.group(1)
        if val not in seen:
            seen.add(val)
            unique_domains.append(m)

    domain_line = ""
    if full_line:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("Target Domain:"):
                # Some layouts put a badge/label on the same line separated
                # by 2+ spaces (column layout) — trim that off.
                domain_line = re.split(r"\s{2,}", stripped)[0]
                break

    rows = []
    for i, dmatch in enumerate(unique_domains):
        domain = dmatch.group(1)
        ip = ip_matches[i].group(1) if i < len(ip_matches) else (
            ip_matches[0].group(1) if ip_matches else ""
        )
        row = {"page": page_num, "target_domain": domain, "resolved_ip": ip}
        if full_line:
            row["domain_line"] = domain_line
        rows.append(row)
    return rows


def stream_extract(txt_path: str, csv_path: str, full_line: bool):
    fieldnames = ["page", "target_domain", "resolved_ip"]
    if full_line:
        fieldnames.append("domain_line")

    page_num = 0
    total_rows = 0
    buffer = []

    with open(txt_path, "r", encoding="utf-8", errors="replace") as infile, \
         open(csv_path, "w", newline="", encoding="utf-8") as outfile:

        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()

        def flush_page():
            nonlocal page_num, total_rows
            if not buffer:
                return
            page_num += 1
            page_text = "".join(buffer)
            buffer.clear()
            rows = process_page(page_num, page_text, full_line)
            for r in rows:
                writer.writerow(r)
            total_rows += len(rows)
            if page_num % 500 == 0:
                print(f"  ...processed {page_num} pages, {total_rows} matches so far", file=sys.stderr)

        # Read in reasonably large chunks and split on form-feed as we go,
        # so we never hold the whole file in memory.
        chunk_size = 1024 * 1024  # 1 MB
        carry = ""
        while True:
            chunk = infile.read(chunk_size)
            if not chunk:
                break
            chunk = carry + chunk
            parts = chunk.split(FORM_FEED)
            # All parts except the last are complete pages
            for part in parts[:-1]:
                buffer.append(part)
                flush_page()
            carry = parts[-1]
        if carry:
            buffer.append(carry)
            flush_page()

    return page_num, total_rows


def main():
    parser = argparse.ArgumentParser(
        description="Fast extraction of Target Domain + Resolved IP pairs from large PDFs into a CSV."
    )
    parser.add_argument("pdf", help="Path to the input PDF")
    parser.add_argument("-o", "--output", default="domain_ip_results.csv", help="Output CSV path")
    parser.add_argument(
        "--full-line", action="store_true",
        help="Include the complete 'Target Domain: ...' line as an extra CSV column"
    )
    parser.add_argument(
        "--keep-text", action="store_true",
        help="Keep the intermediate extracted .txt file instead of deleting it"
    )
    args = parser.parse_args()

    check_pdftotext()

    if not os.path.exists(args.pdf):
        print(f"ERROR: file not found: {args.pdf}", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    txt_fd, txt_path = tempfile.mkstemp(suffix=".txt")
    os.close(txt_fd)

    try:
        print("Extracting text from PDF (pdftotext)...", file=sys.stderr)
        run_pdftotext(args.pdf, txt_path)
        t1 = time.time()
        print(f"  done in {t1 - t0:.1f}s", file=sys.stderr)

        print("Scanning pages for Target Domain / Resolved IP...", file=sys.stderr)
        page_count, row_count = stream_extract(txt_path, args.output, args.full_line)
        t2 = time.time()
        print(f"  done in {t2 - t1:.1f}s", file=sys.stderr)

        print(f"\nProcessed {page_count} pages, extracted {row_count} record(s) -> {args.output}")
        print(f"Total time: {t2 - t0:.1f}s")

        if args.keep_text:
            kept_path = os.path.splitext(args.pdf)[0] + "_extracted.txt"
            shutil.copy(txt_path, kept_path)
            print(f"Intermediate text kept at: {kept_path}")

    except subprocess.CalledProcessError as e:
        print(f"ERROR: pdftotext failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if os.path.exists(txt_path):
            os.remove(txt_path)


if __name__ == "__main__":
    main()