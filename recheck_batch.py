"""
recheck_batch.py — High-performance re-checking runner for Excel/CSV domain batches.

Features:
- Supports .xlsx, .xls, and .csv input files.
- Auto-detects columns (S.No/S_no, Domain/domain, URL/url).
- Async high-throughput fetching (50 concurrent workers).
- Multi-tier classification with banking, educational, government, and .org guardrails.
- Real-time MongoDB sync (cleans false positives by setting exported=False, screenshot_taken=False).
- Auto-saving checkpoint every 25 domains (safe to pause/resume anytime).
- Generates a multi-tab Excel distribution report with Summary, TLD Distribution,
  False Positives (Regular), Confirmed Gambling, Dead Sites, Blocked Sites, and All Results.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

# Silence verbose third-party loggers
for _name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright"):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

from dotenv import load_dotenv
import pandas as pd
from tqdm import tqdm

_ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=_ROOT / ".env")

from db.mongo_client import get_db, checked_domains, source_domains, IST
from checking_url.fetcher import fetch
from checking_url.classifier import load_keywords, classify, _extract_text
from checking_url.ai_classifier import classify_with_challenge, close_ai_session, start_ollama_if_needed


def load_checkpoint(checkpoint_file: Path) -> dict:
    if checkpoint_file.exists():
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"results": {}}


def save_checkpoint(checkpoint_file: Path, data: dict):
    os.makedirs(checkpoint_file.parent, exist_ok=True)
    tmp_path = str(checkpoint_file) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, str(checkpoint_file))


def update_mongo_record(domain: str, url: str, status: str, reason, redirect_url: str = None):
    reason_str = ", ".join(str(r) for r in reason) if isinstance(reason, list) else str(reason)

    if status == "gambling":
        update_checked = {
            "$set": {
                "domain": domain,
                "url": url,
                "status": "gambling",
                "reason": reason_str,
                "redirect_url": redirect_url,
            }
        }
        update_source = {
            "$set": {"domain": domain, "active": True, "processed": True}
        }
    else:  # regular, dead, blocked, unconfirmed
        update_checked = {
            "$set": {
                "domain": domain,
                "url": url,
                "status": status,
                "reason": reason_str,
                "redirect_url": redirect_url,
                "screenshot_taken": False,
                "exported": False,  # Remove from reported gambling exports!
            }
        }
        active_flag = True if status in ("regular", "unconfirmed") else False
        update_source = {
            "$set": {"domain": domain, "active": active_flag, "processed": True}
        }

    try:
        checked_domains().update_one({"_id": domain}, update_checked, upsert=True)
        source_domains().update_one({"_id": domain}, update_source, upsert=True)
    except Exception as e:
        print(f"[!] MongoDB update error for {domain}: {e}")


def export_excel_report(rows: list[dict], output_path: Path):
    os.makedirs(output_path.parent, exist_ok=True)
    df_all = pd.DataFrame(rows)
    total_count = len(df_all)
    if total_count == 0:
        return

    # Extract TLD for distribution breakdown
    def _extract_tld(domain_str):
        d = str(domain_str).strip().lower()
        parts = d.split(".")
        if len(parts) >= 3 and parts[-2] in ("bank", "gov", "ac", "edu", "co", "nic", "res", "gen"):
            return f".{parts[-2]}.{parts[-1]}"
        elif len(parts) >= 2:
            return f".{parts[-1]}"
        return "other"

    df_all["TLD"] = df_all["Domain"].apply(_extract_tld)

    df_false_positives = df_all[df_all["Status"] == "regular"].copy()
    df_gambling = df_all[df_all["Status"] == "gambling"].copy()
    df_dead = df_all[df_all["Status"] == "dead"].copy()
    df_blocked = df_all[df_all["Status"] == "blocked"].copy()
    df_unconfirmed = df_all[df_all["Status"] == "unconfirmed"].copy()
    df_redirected = df_all[df_all["Redirected To"] != "-"].copy()

    # 1. Summary & Status Distribution
    summary_data = [
        {"Category": "Total Domains Evaluated", "Count": total_count, "Percentage": "100.00%"},
        {"Category": "Confirmed Active Gambling", "Count": len(df_gambling), "Percentage": f"{100 * len(df_gambling) / total_count:.2f}%"},
        {"Category": "False Positives (Regular Websites)", "Count": len(df_false_positives), "Percentage": f"{100 * len(df_false_positives) / total_count:.2f}%"},
        {"Category": "Redirected Domains (Tracked)", "Count": len(df_redirected), "Percentage": f"{100 * len(df_redirected) / total_count:.2f}%"},
        {"Category": "Dead / Unreachable / Parked", "Count": len(df_dead), "Percentage": f"{100 * len(df_dead) / total_count:.2f}%"},
        {"Category": "Blocked (Cloudflare WAF / 403)", "Count": len(df_blocked), "Percentage": f"{100 * len(df_blocked) / total_count:.2f}%"},
        {"Category": "Unconfirmed", "Count": len(df_unconfirmed), "Percentage": f"{100 * len(df_unconfirmed) / total_count:.2f}%"},
    ]
    df_summary = pd.DataFrame(summary_data)

    # 2. TLD Distribution Matrix
    tld_summary = []
    top_tlds = df_all["TLD"].value_counts().head(30).index.tolist()
    for tld in top_tlds:
        subset = df_all[df_all["TLD"] == tld]
        t_total = len(subset)
        t_gambling = len(subset[subset["Status"] == "gambling"])
        t_regular = len(subset[subset["Status"] == "regular"])
        t_dead = len(subset[subset["Status"] == "dead"])
        t_blocked = len(subset[subset["Status"] == "blocked"])
        fp_rate = f"{100 * t_regular / t_total:.2f}%" if t_total > 0 else "0.00%"
        tld_summary.append({
            "TLD / Extension": tld,
            "Total Domains": t_total,
            "Confirmed Gambling": t_gambling,
            "False Positives (Regular)": t_regular,
            "Dead": t_dead,
            "Blocked": t_blocked,
            "False Positive Rate": fp_rate,
        })
    df_tld_dist = pd.DataFrame(tld_summary)

    # Write all sheets
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_summary.to_excel(writer, sheet_name="Distribution Summary", index=False)
        df_tld_dist.to_excel(writer, sheet_name="TLD Distribution", index=False)
        df_false_positives.to_excel(writer, sheet_name="False Positives (Regular)", index=False)
        df_gambling.to_excel(writer, sheet_name="Confirmed Gambling", index=False)
        df_redirected.to_excel(writer, sheet_name="Redirected Domains", index=False)
        df_dead.to_excel(writer, sheet_name="Dead Sites", index=False)
        df_blocked.to_excel(writer, sheet_name="Blocked Sites", index=False)
        df_all.to_excel(writer, sheet_name="All Results", index=False)

    print(f"\n[+] Multi-tab Excel distribution report generated: {output_path}")


async def recheck_batch(
    input_file: str,
    concurrency: int = 50,
    timeout: float = 10.0,
    limit: int = 0,
):
    get_db()
    keywords = load_keywords()

    try:
        await start_ollama_if_needed()
    except Exception as e:
        print(f"[!] Ollama notice: {e}")

    in_path = Path(input_file)
    if not in_path.exists():
        print(f"[!] Input file not found: {input_file}")
        return

    safe_stem = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in in_path.stem)
    checkpoint_file = _ROOT / "output" / f"checkpoint_{safe_stem}.json"
    output_excel = _ROOT / "output" / f"{safe_stem}_reclassified_results.xlsx"

    # Load dataframe
    if in_path.suffix.lower() in (".xlsx", ".xls"):
        df_input = pd.read_excel(str(in_path))
    else:
        try:
            with open(str(in_path), "r", encoding="utf-8", errors="ignore") as f:
                first_line = f.readline().strip()
            if "." in first_line and not any(h in first_line.lower() for h in ("domain", "url", "s.no", "s_no", "sno")):
                df_input = pd.read_csv(str(in_path), header=None, names=["domain"])
                df_input["S_no"] = range(1, len(df_input) + 1)
                df_input["url"] = df_input["domain"].apply(lambda d: f"https://{d}")
            else:
                df_input = pd.read_csv(str(in_path))
        except Exception:
            df_input = pd.read_csv(str(in_path))

    # Auto-detect column names
    sno_col = next((c for c in df_input.columns if "s" in str(c).lower() and ("no" in str(c).lower() or "." in str(c))), df_input.columns[0])
    domain_col = next((c for c in df_input.columns if "domain" in str(c).lower()), None)
    url_col = next((c for c in df_input.columns if "url" in str(c).lower()), None)

    if not domain_col:
        domain_col = df_input.columns[1] if len(df_input.columns) > 1 else df_input.columns[0]

    if limit > 0:
        df_input = df_input.head(limit)

    total_rows = len(df_input)
    print(f"\n[+] Loaded {total_rows:,} domains from {in_path.name}")
    print(f"    Columns: S_No='{sno_col}', Domain='{domain_col}', URL='{url_col}'")
    print(f"    Checkpoint: {checkpoint_file.name}")
    print(f"    Output Report: {output_excel.name}")

    checkpoint = load_checkpoint(checkpoint_file)
    results_map = checkpoint.get("results", {})
    print(f"[+] Found {len(results_map):,} domains already completed in checkpoint")

    sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=total_rows, desc=f"Checking {in_path.stem[:15]}", unit="domain", dynamic_ncols=True)
    pbar.update(len(results_map))

    stats = {
        "gambling": sum(1 for r in results_map.values() if r.get("status") == "gambling"),
        "regular": sum(1 for r in results_map.values() if r.get("status") == "regular"),
        "dead": sum(1 for r in results_map.values() if r.get("status") == "dead"),
        "blocked": sum(1 for r in results_map.values() if r.get("status") == "blocked"),
        "unconfirmed": sum(1 for r in results_map.values() if r.get("status") == "unconfirmed"),
    }

    save_counter = 0
    save_lock = asyncio.Lock()

    async def process_item(item: dict):
        nonlocal save_counter
        s_no = item.get(sno_col)
        raw_domain = str(item.get(domain_col, "")).strip().lower()
        if not raw_domain or raw_domain == "nan":
            pbar.update(1)
            return

        domain = raw_domain.replace("https://", "").replace("http://", "").removeprefix("www.").rstrip("/")
        if url_col and item.get(url_col):
            url = str(item.get(url_col)).strip()
        else:
            url = f"https://{domain}"

        # Check if already processed
        if domain in results_map:
            return

        final_status = "regular"
        final_reason = "No reason"
        final_redirect_url = None

        async with sem:
            try:
                fetch_res = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=0.3)
                final_redirect_url = fetch_res.final_url if fetch_res.redirected else None
                effective_url = fetch_res.final_url or url

                if fetch_res.failure_type:
                    if fetch_res.failure_type == "blocked":
                        final_status = "blocked"
                        final_reason = "Blocked: Cloudflare WAF / 403 Forbidden"
                    else:
                        final_status = "dead"
                        final_reason = f"Dead: {fetch_res.error or fetch_res.failure_type}"
                else:
                    decision, matched_kw = classify(fetch_res.html or "", url=effective_url, keywords=keywords)

                    prefix = f"[Redirected to: {final_redirect_url}] " if final_redirect_url else ""
                    if decision == "gambling":
                        final_status = "gambling"
                        final_reason = f"{prefix}{len(matched_kw)} gambling keywords matched: {', '.join(matched_kw[:3])}"
                    elif decision == "needs_ai":
                        ai_res = await classify_with_challenge(
                            fetch_res.html or "",
                            url=effective_url,
                            matched_keywords=matched_kw,
                            fast_mode=True,
                        )
                        final_status = ai_res.get("verdict", "regular")
                        final_reason = f"{prefix}{ai_res.get('reason', 'AI evaluated')}"
                    else:
                        final_status = "regular"
                        final_reason = f"{prefix}{len(matched_kw)} keywords matched (regular/safe)"
            except Exception as e:
                final_status = "unconfirmed"
                final_reason = f"Recheck error: {e}"

        # Update MongoDB
        update_mongo_record(domain, url, final_status, final_reason, redirect_url=final_redirect_url)

        results_map[domain] = {
            "S_no": s_no,
            "domain": domain,
            "url": url,
            "redirect_url": final_redirect_url,
            "status": final_status,
            "reason": final_reason,
            "checked_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        }
        stats[final_status] = stats.get(final_status, 0) + 1

        async with save_lock:
            save_counter += 1
            if save_counter >= 25:
                save_checkpoint(checkpoint_file, {"results": results_map})
                save_counter = 0

        pbar.update(1)
        pbar.set_postfix({
            "Gambling": stats.get("gambling", 0),
            "Regular (FP)": stats.get("regular", 0),
            "Dead": stats.get("dead", 0),
            "Blocked": stats.get("blocked", 0),
        })

    tasks = []
    for _, row in df_input.iterrows():
        tasks.append(process_item(row.to_dict()))

    try:
        chunk_size = 500
        for i in range(0, len(tasks), chunk_size):
            chunk = tasks[i:i + chunk_size]
            await asyncio.gather(*chunk)
    finally:
        save_checkpoint(checkpoint_file, {"results": results_map})
        pbar.close()
        await close_ai_session()

        # Prepare final ordered dataset matching original file
        output_rows = []
        for _, row in df_input.iterrows():
            d = str(row.get(domain_col, "")).strip().lower().replace("https://", "").replace("http://", "").removeprefix("www.").rstrip("/")
            res = results_map.get(d, {})
            output_rows.append({
                "S_no": row.get(sno_col),
                "Domain": d,
                "Original URL": row.get(url_col) if url_col else f"https://{d}",
                "Redirected To": res.get("redirect_url") or "-",
                "Status": res.get("status", "unprocessed"),
                "Reason / Signals": res.get("reason", "Not processed"),
                "Checked At": res.get("checked_at", ""),
            })

        export_excel_report(output_rows, output_excel)

    print("\n" + "=" * 60)
    print(f"       RE-CHECK SUMMARY: {in_path.name}       ")
    print("=" * 60)
    print(f" Total Domains in File          : {len(output_rows):,}")
    print(f" Total Evaluated                : {len(results_map):,}")
    print(f"  * Confirmed Gambling Sites    : {stats.get('gambling', 0):,}")
    print(f"  * False Positives (Regular)   : {stats.get('regular', 0):,}")
    print(f"  * Dead / Unreachable Sites    : {stats.get('dead', 0):,}")
    print(f"  * Blocked Sites (WAF/403)     : {stats.get('blocked', 0):,}")
    print(f"  * Unconfirmed Sites           : {stats.get('unconfirmed', 0):,}")
    print("=" * 60)
    print(f"[+] Distribution Report Saved: {output_excel}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-evaluate domain batch and generate Distribution Excel report")
    parser.add_argument("--input", default="Gambling sites batch-4 6974.xlsx", help="Path to input Excel/CSV file")
    parser.add_argument("--concurrency", type=int, default=50, help="Number of concurrent workers")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of domains (0 for all)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Fetch timeout per domain in seconds")
    args = parser.parse_args()

    asyncio.run(recheck_batch(input_file=args.input, concurrency=args.concurrency, limit=args.limit, timeout=args.timeout))
