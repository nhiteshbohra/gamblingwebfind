"""
reports/excel_exporter.py — Export checked_domains collection to clean Excel workbooks.

Two functions:
  export_verify_workbook()  — 4 sheets (Gambling / Blocked / Dead / Regular)
  export_capture_workbook() — 2 sheets (Captured / Failed)
"""
import os
from datetime import datetime, timezone, timedelta
import pandas as pd
from db.mongo_client import checked_domains

IST = timezone(timedelta(hours=5, minutes=30))


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


def export_capture_workbook(domain_ids: list = None, output_path: str = "output/capture_results.xlsx") -> dict:
    """2-sheet workbook for capture mode results (Captured / Failed)."""
    if domain_ids is not None and len(domain_ids) == 0:
        print("[export] No domains processed in this run to export.")
        return {"file": output_path, "captured": 0, "failed": 0}

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    base_filter = {"_id": {"$in": domain_ids}} if domain_ids else {}

    captured_filter = {**base_filter, "screenshot_taken": True}
    failed_filter   = {**base_filter, "screenshot_taken": False}

    captured = list(checked_domains().find(captured_filter))
    failed   = list(checked_domains().find(failed_filter))

    # Excel Captured sheet only includes domains whose JPEG is on disk
    # (matches the Word doc screenshot count exactly)
    screenshots_dir = os.path.join("output", "screenshots")
    try:
        from capture_url.screenshot import _url_to_filename
        captured = [d for d in captured if os.path.exists(
            os.path.join(screenshots_dir, _url_to_filename(d.get("url", "")))
        )]
    except Exception:
        pass  # fallback: include all if import fails

    cap_rows = [
        {
            "S.No.": i,
            "Domain": d.get("_id", ""),
            "URL": d.get("url", ""),
        }
        for i, d in enumerate(captured, 1)
    ]

    fail_rows = [
        {
            "S.No.": i,
            "Domain": d.get("_id", ""),
            "URL": d.get("url", ""),
            "Failure Reason": d.get("screenshot_failed_reason", "Timeout / Navigation error"),
        }
        for i, d in enumerate(failed, 1)
    ]

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        pd.DataFrame(cap_rows).to_excel(writer, sheet_name='Captured', index=False)
        pd.DataFrame(fail_rows).to_excel(writer, sheet_name='Failed', index=False)

    cap_ids = [d["_id"] for d in captured]
    if cap_ids:
        now_ist = datetime.now(IST).strftime("%Y-%m-%d")
        checked_domains().update_many(
            {"_id": {"$in": cap_ids}},
            {"$set": {"exported": True, "exported_at": now_ist}}
        )

    fail_ids = [d["_id"] for d in failed]
    if fail_ids:
        checked_domains().update_many(
            {"_id": {"$in": fail_ids}},
            {"$set": {"exported": False}, "$unset": {"export_status": "", "exported_at": ""}}
        )

    print(f"[export] capture workbook -> {output_path}  captured={len(captured)} failed={len(failed)}")
    return {"file": output_path, "captured": len(captured), "failed": len(failed)}
