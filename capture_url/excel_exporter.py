"""
capture_url/excel_exporter.py — Export checked_domains collection to clean Excel workbooks.

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


def export_capture_workbook(
    domain_ids: list = None,
    output_dir: str = "output",
    batch_size: int = 40,
) -> dict:
    """Batched 2-sheet workbook for capture mode results (Captured / Failed).

    Splits successfully captured domains into batches of `batch_size` (default 40).
    For each batch, creates:
        output_dir/batch_001/batch_001_domains.xlsx
        output_dir/batch_002/batch_002_domains.xlsx
        ...

    S.No. restarts from 1 in each batch (each batch is a standalone deliverable).
    Failed domains are written once to output_dir/failed_domains.xlsx (not batched).

    Returns dict with total captured, failed, batch count, and file list.
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

    # Only include captured domains whose JPEG is on disk (matches Word doc)
    screenshots_dir = os.path.join("output", "screenshots")
    try:
        from capture_url.screenshot import _url_to_filename
        captured = [d for d in captured if os.path.exists(
            os.path.join(screenshots_dir, _url_to_filename(d.get("url", "")))
        )]
    except Exception:
        pass  # fallback: include all if import fails

    # ── Batch captured into groups of batch_size ──────────────────────────────
    batches = [captured[i:i + batch_size] for i in range(0, len(captured), batch_size)]
    total_batches = len(batches)
    generated_files = []

    now_ist = datetime.now(IST).strftime("%Y-%m-%d")

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

        # Mark these domains as exported in MongoDB
        cap_ids = [d["_id"] for d in batch_docs]
        if cap_ids:
            checked_domains().update_many(
                {"_id": {"$in": cap_ids}},
                {"$set": {"exported": True, "exported_at": now_ist}}
            )

        print(f"[export] {batch_label}: {len(batch_docs)} domains -> {xlsx_path}")
        generated_files.append(xlsx_path)

    # ── Write all failed domains to a single non-batched file ─────────────────
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
        print(f"[export] Failed domains ({len(failed)}) -> {failed_xlsx}")
        generated_files.append(failed_xlsx)

        fail_ids = [d["_id"] for d in failed]
        checked_domains().update_many(
            {"_id": {"$in": fail_ids}},
            {"$set": {"exported": False}, "$unset": {"export_status": "", "exported_at": ""}}
        )

    print(f"[export] Done. {total_batches} batch Excel file(s) + {1 if failed else 0} failed file. Folder: {output_dir}")
    return {
        "captured": len(captured),
        "failed": len(failed),
        "batches": total_batches,
        "files": generated_files,
    }

