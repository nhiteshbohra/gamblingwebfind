"""
capture_url/excel_exporter.py — Export checked_domains collection to clean Excel workbooks.

Two functions:
  export_verify_workbook()  — 4 sheets (Gambling / Blocked / Dead / Regular)
  export_capture_workbook() — 2 sheets (Captured / Failed)
"""
import os
from datetime import datetime, timezone, timedelta
import pandas as pd
import openpyxl
from openpyxl.styles import Font
from db.mongo_client import checked_domains

IST = timezone(timedelta(hours=5, minutes=30))
HYPERLINK_FONT = Font(color="0000FF", underline="single")


import urllib.parse

def _make_excel_urls_clickable(workbook_path: str):
    """Format Domain and URL columns in all sheets of an Excel file to be clickable hyperlinks."""
    try:
        wb = openpyxl.load_workbook(workbook_path)
        for sheetname in wb.sheetnames:
            ws = wb[sheetname]
            url_col_idx = None
            domain_col_idx = None

            for col_idx, cell in enumerate(ws[1], 1):
                col_name = str(cell.value or "").strip().lower()
                if col_name == "url":
                    url_col_idx = col_idx
                elif col_name == "domain":
                    domain_col_idx = col_idx

            if url_col_idx:
                for row in range(2, ws.max_row + 1):
                    cell = ws.cell(row=row, column=url_col_idx)
                    val = str(cell.value or "").strip()
                    if val and not val.startswith("#"):
                        target = val if (val.startswith("http://") or val.startswith("https://")) else f"https://{val}"
                        try:
                            # Validate URL is parseable
                            parsed = urllib.parse.urlparse(target)
                            if parsed.netloc:
                                cell.hyperlink = target
                                cell.font = HYPERLINK_FONT
                        except Exception:
                            pass

            if domain_col_idx:
                for row in range(2, ws.max_row + 1):
                    cell = ws.cell(row=row, column=domain_col_idx)
                    val = str(cell.value or "").strip()
                    if val and not val.startswith("#"):
                        target = val if (val.startswith("http://") or val.startswith("https://")) else f"https://{val}"
                        try:
                            parsed = urllib.parse.urlparse(target)
                            if parsed.netloc:
                                cell.hyperlink = target
                                cell.font = HYPERLINK_FONT
                        except Exception:
                            pass

        wb.save(workbook_path)
    except Exception as e:
        print(f"[export] Warning: Could not apply hyperlinks to {workbook_path}: {e}")


def _to_df(docs):
    rows = []
    for i, d in enumerate(docs, 1):
        rows.append({
            "S.No.": i,
            "Domain": d.get("_id", ""),
            "URL": d.get("url", ""),
        })
    return pd.DataFrame(rows)


def export_verify_workbook(output_path: str = "output/verify_results.xlsx") -> dict:
    """4-sheet workbook for check mode results. Clean S.No., Domain, URL columns."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    sheets = {
        "Gambling": list(checked_domains().find({"status": "gambling"})),
        "Blocked":  list(checked_domains().find({"status": "blocked"})),
        "Dead":     list(checked_domains().find({"status": "dead"})),
        "Regular":  list(checked_domains().find({"status": "regular"})),
    }

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        for sheet_name, docs in sheets.items():
            _to_df(docs).to_excel(writer, sheet_name=sheet_name, index=False)

    _make_excel_urls_clickable(output_path)

    all_ids = [d["_id"] for docs in sheets.values() for d in docs]
    if all_ids:
        now_ist = datetime.now(IST).strftime("%Y-%m-%d")
        checked_domains().update_many(
            {"_id": {"$in": all_ids}},
            {"$set": {"exported": True, "exported_at": now_ist}}
        )

    counts = {name: len(docs) for name, docs in sheets.items()}
    print(f"[export] verify workbook -> {output_path}  {counts}")
    return {"file": output_path, **counts}


def export_capture_workbook(
    domain_ids: list = None,
    output_dir: str = "output",
    batch_size: int = 40,
    single_file: bool = False,
    combined: bool = False,
) -> dict:
    """Export captured domains to Excel workbooks.

    Supports:
      - Single file mode (single_file=True or batch_size=0): 1 combined Excel file.
      - Batch mode (batch_size > 0): splits into batch_001, batch_002 folders.
      - Both mode (combined=True): writes batches AND master combined Excel file.
    """
    if domain_ids is not None and len(domain_ids) == 0:
        print("[export] No domains processed in this run to export.")
        return {"captured": 0, "failed": 0, "batches": 0, "files": []}

    os.makedirs(output_dir, exist_ok=True)

    base_filter = {"_id": {"$in": domain_ids}} if domain_ids else {}
    captured_filter = {**base_filter, "screenshot_taken": True}
    failed_filter   = {**base_filter, "screenshot_taken": False}

    captured = list(checked_domains().find(captured_filter))
    failed   = list(checked_domains().find(failed_filter))

    # Only include captured domains whose JPEG is on disk and valid
    screenshots_dir = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))
    try:
        from capture_url.screenshot import _url_to_filename, is_valid_screenshot
        verified_captured = []
        missing_ids = []
        for d in captured:
            domain = d.get("domain") or d.get("_id")
            url = d.get("url") or f"https://{domain}"
            candidates = [
                _url_to_filename(url),
                _url_to_filename(f"https://{domain}"),
                _url_to_filename(f"http://{domain}"),
            ]
            if any(os.path.exists(os.path.join(screenshots_dir, c)) and is_valid_screenshot(os.path.join(screenshots_dir, c)) for c in candidates):
                verified_captured.append(d)
            else:
                missing_ids.append(d["_id"])

        if missing_ids:
            checked_domains().update_many(
                {"_id": {"$in": missing_ids}},
                {"$set": {"screenshot_taken": False, "screenshot_failed_reason": "Screenshot JPEG missing on disk"}}
            )
        captured = verified_captured
    except Exception:
        pass

    generated_files = []
    now_ist = datetime.now(IST).strftime("%Y-%m-%d")

    # ── Single File / Combined Master File ────────────────────────────────────
    if single_file or batch_size <= 0 or combined:
        master_xlsx = os.path.join(output_dir, "captured_domains_combined.xlsx")
        master_rows = [
            {
                "S.No.": i,
                "Domain": d.get("_id", ""),
                "URL": d.get("url", ""),
            }
            for i, d in enumerate(captured, 1)
        ]
        with pd.ExcelWriter(master_xlsx, engine='openpyxl') as writer:
            pd.DataFrame(master_rows).to_excel(writer, sheet_name='Captured', index=False)

        _make_excel_urls_clickable(master_xlsx)
        generated_files.append(master_xlsx)
        print(f"[export] Single combined Excel: {len(captured)} domains -> {master_xlsx}")

        cap_ids = [d["_id"] for d in captured]
        if cap_ids:
            checked_domains().update_many(
                {"_id": {"$in": cap_ids}},
                {"$set": {"exported": True, "exported_at": now_ist}}
            )

    # ── Batched Export ────────────────────────────────────────────────────────
    total_batches = 0
    if not single_file and batch_size > 0:
        batches = [captured[i:i + batch_size] for i in range(0, len(captured), batch_size)]
        total_batches = len(batches)

        for batch_num, batch_docs in enumerate(batches, 1):
            batch_label = f"batch_{batch_num:03d}"
            batch_dir = os.path.join(output_dir, batch_label)
            os.makedirs(batch_dir, exist_ok=True)

            xlsx_path = os.path.join(batch_dir, f"{batch_label}_domains.xlsx")

            cap_rows = [
                {
                    "S.No.": i,
                    "Domain": d.get("_id", ""),
                    "URL": d.get("url", ""),
                }
                for i, d in enumerate(batch_docs, 1)
            ]

            with pd.ExcelWriter(xlsx_path, engine='openpyxl') as writer:
                pd.DataFrame(cap_rows).to_excel(writer, sheet_name='Captured', index=False)

            _make_excel_urls_clickable(xlsx_path)

            cap_ids = [d["_id"] for d in batch_docs]
            if cap_ids:
                checked_domains().update_many(
                    {"_id": {"$in": cap_ids}},
                    {"$set": {"exported": True, "exported_at": now_ist}}
                )

            print(f"[export] {batch_label}: {len(batch_docs)} domains -> {xlsx_path}")
            generated_files.append(xlsx_path)

    # ── Write Failed Domains ──────────────────────────────────────────────────
    if failed:
        fail_rows = [
            {
                "S.No.": i,
                "Domain": d.get("_id", ""),
                "URL": d.get("url", ""),
                "Failure Reason": d.get("screenshot_failed_reason", "Timeout / Navigation error"),
            }
            for i, d in enumerate(failed, 1)
        ]
        failed_xlsx = os.path.join(output_dir, "failed_domains.xlsx")
        with pd.ExcelWriter(failed_xlsx, engine='openpyxl') as writer:
            pd.DataFrame(fail_rows).to_excel(writer, sheet_name='Failed', index=False)

        _make_excel_urls_clickable(failed_xlsx)
        print(f"[export] Failed domains ({len(failed)}) -> {failed_xlsx}")
        generated_files.append(failed_xlsx)

        fail_ids = [d["_id"] for d in failed]
        checked_domains().update_many(
            {"_id": {"$in": fail_ids}},
            {"$set": {"exported": False}, "$unset": {"exported_at": ""}}
        )

    print(f"[export] Done. Generated {len(generated_files)} file(s). Output: {output_dir}")
    return {
        "captured": len(captured),
        "failed": len(failed),
        "batches": total_batches,
        "files": generated_files,
    }



