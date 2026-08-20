#!/usr/bin/env python3
"""
check_domains.py
-----------------
Reads a list of domains/websites from an Excel file, checks whether each one
is reachable (Up) or not (Down/Blocked), and writes the results to a new
Excel file.

USAGE (basic):
    python check_domains.py input.xlsx

USAGE (with options):
    python check_domains.py input.xlsx \
        --output results.xlsx \
        --sheet "Sheet1" \
        --column "Domain" \
        --timeout 8 \
        --workers 20

REQUIREMENTS (install once):
    pip install pandas openpyxl requests

WHAT COUNTS AS "DOWN / BLOCKED":
    - DNS lookup fails (domain doesn't resolve)          -> Down/Blocked
    - Connection times out                                -> Down/Blocked
    - Connection actively refused / reset                 -> Down/Blocked
    - SSL handshake fails                                  -> Down/Blocked
    - Any HTTP response comes back (even 403/404/500)     -> Up
      (an HTTP response means the site IS reachable, even if that
       particular page errors out)

NOTE ON ISP-LEVEL BLOCKING:
    Some blocking methods (DNS hijacking to a "this site has been blocked by
    order of ..." page) will still return a valid HTTP response and will show
    as "Up" here, since technically something answered. If you want the script
    to also flag pages whose content contains block-page keywords (e.g.
    "blocked", "restricted", your regulator's name, etc.), tell me and I'll
    add a keyword-match check on the page content.
"""

import argparse
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import requests
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    SSLError,
    Timeout,
    RequestException,
)

# Some sites block requests that don't look like a real browser.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def clean_domain(raw: str) -> str:
    """Strip scheme, path, and whitespace so we just get the bare domain."""
    d = str(raw).strip()
    d = d.replace("http://", "").replace("https://", "")
    d = d.split("/")[0].strip()
    return d


def check_domain(domain: str, timeout: int) -> dict:
    """Check a single domain. Tries HTTPS first, then HTTP."""
    domain = clean_domain(domain)
    result = {
        "Domain": domain,
        "Status": None,
        "HTTP Code": None,
        "Notes": None,
        "Checked At": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if not domain:
        result["Status"] = "Skipped"
        result["Notes"] = "Empty/invalid domain"
        return result

    # Quick DNS check first — if this fails, no point trying HTTP(S).
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname(domain)
    except socket.gaierror:
        result["Status"] = "Down/Blocked"
        result["Notes"] = "DNS lookup failed (domain does not resolve)"
        return result
    except socket.timeout:
        result["Status"] = "Down/Blocked"
        result["Notes"] = "DNS lookup timed out"
        return result

    for scheme in ("https://", "http://"):
        url = scheme + domain
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers=HEADERS,
                allow_redirects=True,
                verify=True,
            )
            result["Status"] = "Up"
            result["HTTP Code"] = resp.status_code
            result["Notes"] = f"Reachable via {scheme.strip('://')}"
            return result
        except SSLError:
            # Try again without cert verification before giving up on this scheme
            try:
                resp = requests.get(
                    url, timeout=timeout, headers=HEADERS,
                    allow_redirects=True, verify=False,
                )
                result["Status"] = "Up"
                result["HTTP Code"] = resp.status_code
                result["Notes"] = f"Reachable via {scheme.strip('://')} (SSL cert invalid)"
                return result
            except RequestException:
                continue
        except Timeout:
            result["Status"] = "Down/Blocked"
            result["Notes"] = f"Timed out ({scheme.strip('://')})"
            continue
        except ReqConnectionError:
            result["Status"] = "Down/Blocked"
            result["Notes"] = f"Connection refused/reset ({scheme.strip('://')})"
            continue
        except RequestException as e:
            result["Status"] = "Down/Blocked"
            result["Notes"] = f"Error ({scheme.strip('://')}): {e}"
            continue

    if result["Status"] is None:
        result["Status"] = "Down/Blocked"
        result["Notes"] = "Unreachable via HTTPS and HTTP"

    return result


def find_domain_column(df: pd.DataFrame, user_choice: str | None) -> str:
    if user_choice:
        if user_choice not in df.columns:
            sys.exit(f"Column '{user_choice}' not found. Available columns: {list(df.columns)}")
        return user_choice

    candidates = [c for c in df.columns if any(
        key in str(c).lower() for key in ("domain", "website", "url", "site")
    )]
    if candidates:
        return candidates[0]

    # Fall back to the first column
    return df.columns[0]


def main():
    parser = argparse.ArgumentParser(description="Check whether domains in an Excel file are up or down/blocked.")
    parser.add_argument("input_file", help="Path to the input Excel file (.xlsx)")
    parser.add_argument("--output", default="domain_check_results.xlsx", help="Output Excel file path")
    parser.add_argument("--sheet", default=0, help="Sheet name or index to read (default: first sheet)")
    parser.add_argument("--column", default=None, help="Column name containing domains (auto-detected if omitted)")
    parser.add_argument("--timeout", type=int, default=8, help="Timeout in seconds per request (default: 8)")
    parser.add_argument("--workers", type=int, default=20, help="Number of parallel checks (default: 20)")
    args = parser.parse_args()

    print(f"Reading '{args.input_file}' ...")
    df = pd.read_excel(args.input_file, sheet_name=args.sheet)

    col = find_domain_column(df, args.column)
    print(f"Using column: '{col}'")

    domains = [d for d in df[col].dropna().tolist()]
    print(f"Found {len(domains)} domains. Checking (this may take a bit)...")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(check_domain, d, args.timeout): d for d in domains}
        done = 0
        for future in as_completed(futures):
            results.append(future.result())
            done += 1
            if done % 10 == 0 or done == len(domains):
                print(f"  Checked {done}/{len(domains)}")

    results_df = pd.DataFrame(results)

    # Keep the original order (as_completed finishes out of order)
    order = {clean_domain(d): i for i, d in enumerate(domains)}
    results_df["_order"] = results_df["Domain"].map(order)
    results_df = results_df.sort_values("_order").drop(columns="_order").reset_index(drop=True)

    results_df.to_excel(args.output, index=False)

    up = (results_df["Status"] == "Up").sum()
    down = (results_df["Status"] == "Down/Blocked").sum()
    skipped = (results_df["Status"] == "Skipped").sum()

    print("\n--- Summary ---")
    print(f"Up:           {up}")
    print(f"Down/Blocked: {down}")
    print(f"Skipped:      {skipped}")
    print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
