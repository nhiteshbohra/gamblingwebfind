#!/usr/bin/env python3
"""
setup.py — One-command setup for the Gambling Website Hunter.

Installs Python dependencies and downloads the Playwright Chromium browser.

Usage:
    python setup.py
"""

import os
import subprocess
import sys


def run(cmd: list[str], desc: str):
    """Run a command and print status."""
    print(f"\n{'─'*60}")
    print(f"  {desc}")
    print(f"{'─'*60}")
    try:
        subprocess.run(cmd, check=True)
        print(f"  ✅ {desc} — done")
    except subprocess.CalledProcessError as e:
        print(f"  ❌ {desc} — FAILED (exit code {e.returncode})")
        sys.exit(1)


def main():
    print(r"""
╔══════════════════════════════════════════════════════════════╗
║          🚀  GAMBLING HUNTER SETUP  🚀                       ║
║    Installing dependencies and browser...                    ║
╚══════════════════════════════════════════════════════════════╝
    """)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    req_file = os.path.join(base_dir, "requirements.txt")

    # Step 1: Install Python packages
    run(
        [sys.executable, "-m", "pip", "install", "-r", req_file],
        "Installing Python packages from requirements.txt"
    )

    # Step 2: Install Playwright Chromium browser
    run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        "Installing Playwright Chromium browser"
    )

    # Step 3: Create output directories
    output_dir = os.path.join(base_dir, "output")
    screenshots_dir = os.path.join(output_dir, "screenshots")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(screenshots_dir, exist_ok=True)
    print(f"\n  ✅ Output directories created: {output_dir}")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║          ✅  SETUP COMPLETE  ✅                               ║
║                                                              ║
║  You can now run the tool:                                   ║
║                                                              ║
║    python gambling_hunter.py --auto                          ║
║    python gambling_hunter.py --domains domains.txt           ║
║    python gambling_hunter.py --ips iplist.txt                ║
║    python gambling_hunter.py --help                          ║
╚══════════════════════════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    main()
