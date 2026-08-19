from fastapi import APIRouter
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
from db.mongo_client import source_domains, checked_domains, get_db

router = APIRouter(prefix="/api")
IST = timezone(timedelta(hours=5, minutes=30))
OUTPUT_DIR = "output"


@router.get("/stats")
def get_stats():
    """Live aggregation of pipeline metrics from MongoDB and output directory."""
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    week_ago_str = (now_ist - timedelta(days=7)).strftime("%Y-%m-%d")

    # ── 1. domain_Listed (Source domains) ─────────────────────────────────────
    try:
        src = source_domains()
        total_listed = src.estimated_document_count()
        active_listed = src.count_documents({"active": True})
        inactive_listed = src.count_documents({"active": False})
        blocked_source = src.count_documents({"$or": [{"block_reason": "blocked"}, {"active": "blocked"}]})
        dead_source = src.count_documents({"active": False, "block_reason": {"$exists": False}})
        processed_listed = src.count_documents({"processed": True})
        pending_listed = src.count_documents({"active": True, "processed": {"$ne": True}})

        # Source breakdown
        src_groups = list(src.aggregate([
            {"$group": {"_id": "$source", "count": {"$sum": 1}}}
        ]))
        by_source = {str(g["_id"] or "unspecified"): g["count"] for g in src_groups}

        added_today_listed = src.count_documents({"added_date": today_str})
        added_week_listed = src.count_documents({"added_date": {"$gte": week_ago_str}})
    except Exception as e:
        total_listed = active_listed = inactive_listed = blocked_source = dead_source = processed_listed = pending_listed = 0
        by_source = {}
        added_today_listed = added_week_listed = 0

    # ── 2. checked_domains (Classification results) ───────────────────────────
    try:
        chk = checked_domains()
        total_checked = chk.estimated_document_count()

        status_groups = list(chk.aggregate([
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]))
        status_map = {str(g["_id"]): g["count"] for g in status_groups}

        gambling = status_map.get("gambling", 0)
        regular = status_map.get("regular", 0)
        blocked = status_map.get("blocked", 0)
        dead = status_map.get("dead", 0)
        unconfirmed = status_map.get("unconfirmed", 0)

        # Screenshots
        screenshot_taken = chk.count_documents({"status": "gambling", "screenshot_taken": True})
        screenshot_pending = chk.count_documents({
            "status": "gambling",
            "$or": [
                {"screenshot_taken": False},
                {"screenshot_failed_reason": {"$ne": None}},
            ]
        })

        # Failure reasons breakdown
        fail_groups = list(chk.aggregate([
            {"$match": {"status": "gambling", "screenshot_failed_reason": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$screenshot_failed_reason", "count": {"$sum": 1}}}
        ]))
        failed_reasons = {str(g["_id"]): g["count"] for g in fail_groups}

        # Exports
        exported = chk.count_documents({"exported": True})
        pending_export = chk.count_documents({
            "status": "gambling",
            "screenshot_taken": {"$in": [True, "true", "True"]},
            "exported": {"$ne": True}
        })

        # Rates & AI vs Keyword
        rate = round((gambling / total_checked * 100), 2) if total_checked > 0 else 0.0

        ai_classified = chk.count_documents({
            "reason": {"$regex": "AI|ollama|Validator|Challenge|round1|round2", "$options": "i"}
        })
        keyword_classified = max(0, total_checked - ai_classified)

    except Exception as e:
        total_checked = gambling = regular = blocked = dead = unconfirmed = 0
        screenshot_taken = screenshot_pending = exported = pending_export = 0
        failed_reasons = {}
        rate = 0.0
        ai_classified = keyword_classified = 0

    # ── 3. output/ (Generated Reports) ────────────────────────────────────────
    report_runs = 0
    docx_count = 0
    pdf_count = 0
    xlsx_count = 0
    last_export_ts = None
    last_mtime = 0

    if os.path.exists(OUTPUT_DIR):
        run_dirs = []
        for item in os.listdir(OUTPUT_DIR):
            p = os.path.join(OUTPUT_DIR, item)
            if os.path.isdir(p) and item != "screenshots":
                run_dirs.append((item, p, os.path.getmtime(p)))

        report_runs = len(run_dirs)

        for root, dirs, files in os.walk(OUTPUT_DIR):
            dirs[:] = [d for d in dirs if d != "screenshots"]
            for f in files:
                ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
                if ext == "docx":
                    docx_count += 1
                elif ext == "pdf":
                    pdf_count += 1
                elif ext == "xlsx":
                    xlsx_count += 1

                fp = os.path.join(root, f)
                try:
                    mt = os.path.getmtime(fp)
                    if mt > last_mtime:
                        last_mtime = mt
                except OSError:
                    pass

        if last_mtime > 0:
            last_export_ts = datetime.fromtimestamp(last_mtime, tz=IST).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "source_domains": {
            "total_listed": total_listed,
            "active": active_listed,
            "inactive": inactive_listed,
            "blocked_source": blocked_source,
            "dead_source": dead_source,
            "processed": processed_listed,
            "pending": pending_listed,
            "by_source": by_source,
            "added_today": added_today_listed,
            "added_this_week": added_week_listed,
        },
        "checked_domains": {
            "total_checked": total_checked,
            "gambling": gambling,
            "regular": regular,
            "blocked": blocked,
            "dead": dead,
            "unconfirmed": unconfirmed,
            "screenshot_taken": screenshot_taken,
            "screenshot_pending": screenshot_pending,
            "failed_reasons": failed_reasons,
            "exported": exported,
            "pending_export": pending_export,
            "gambling_rate": rate,
            "ai_classified": ai_classified,
            "keyword_classified": keyword_classified,
        },
        "reports": {
            "runs": report_runs,
            "docx_files": docx_count,
            "pdf_files": pdf_count,
            "xlsx_files": xlsx_count,
            "total_files": docx_count + pdf_count + xlsx_count,
            "last_export_timestamp": last_export_ts,
        }
    }
