"""
reports/excel_exporter.py — Export checked_domains collection to clean Excel workbooks.

Two functions:
  export_verify_workbook()  — 4 sheets (Gambling / Blocked / Dead / Regular)
  export_capture_workbook() — 2 sheets (Captured / Failed)
"""
import os
import pandas as pd
from db.mongo_client import checked_domains


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
        checked_domains().update_many({"_id": {"$in": all_ids}}, {"$set": {"exported": True}})

    counts = {name: len(docs) for name, docs in sheets.items()}
    print(f"[export] verify workbook -> {output_path}  {counts}")
    return {"file": output_path, **counts}


def export_capture_workbook(output_path: str = "output/capture_results.xlsx") -> dict:
    """2-sheet workbook for capture mode results (Captured / Failed)."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    captured = list(checked_domains().find({"status": "gambling", "screenshot_taken": True}))
    failed   = list(checked_domains().find({"status": "gambling", "screenshot_taken": False}))

    cap_rows = [
        {"S.No.": i, "Domain": d.get("_id", ""), "URL": d.get("url", "")}
        for i, d in enumerate(captured, 1)
    ]

    fail_rows = [
        {"S.No.": i, "Domain": d.get("_id", ""), "URL": d.get("url", ""), "Failure Reason": d.get("screenshot_failed_reason", "Timeout / Navigation error")}
        for i, d in enumerate(failed, 1)
    ]

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        pd.DataFrame(cap_rows).to_excel(writer, sheet_name='Captured', index=False)
        pd.DataFrame(fail_rows).to_excel(writer, sheet_name='Failed', index=False)

    all_ids = [d["_id"] for d in captured + failed]
    if all_ids:
        checked_domains().update_many({"_id": {"$in": all_ids}}, {"$set": {"exported": True}})

    print(f"[export] capture workbook -> {output_path}  captured={len(captured)} failed={len(failed)}")
    return {"file": output_path, "captured": len(captured), "failed": len(failed)}
