from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio, os, sys
from pathlib import Path
from api.jobs import (
    new_job, get_job, all_jobs, finish_job, is_stage_running,
    capture_prints, set_job_future, stop_stage, stop_job, stop_all
)

router = APIRouter(prefix="/api")

class CheckBody(BaseModel):
    mode: str = "new"

class ExportBody(BaseModel):
    format: int = 1
    batch_size: int = 40

class ScreenshotBody(BaseModel):
    mode: str = "db"
    domains: list = []

class ImportBody(BaseModel):
    domains: list = []

def _get_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.new_event_loop()

def _run_keywords(job_id, loop):
    try:
        from keywordssearch.searxng_search import run_search
        import json
        kw_path = Path(__file__).resolve().parent.parent / "gambling_top_500_keywords.json"
        with open(kw_path) as f:
            keywords = [str(k).strip().lower() for k in json.load(f) if str(k).strip()]
        with capture_prints(job_id, loop):
            fut = asyncio.run_coroutine_threadsafe(
                asyncio.ensure_future(run_search(keywords), loop=loop), loop
            )
            set_job_future(job_id, fut)
            fut.result(timeout=3600)
        finish_job(job_id, "done")
    except asyncio.CancelledError:
        finish_job(job_id, "stopped")
    except Exception as e:
        job = get_job(job_id)
        if job and job.get("status") == "stopped":
            finish_job(job_id, "stopped")
        else:
            finish_job(job_id, "failed")
            if job:
                job["queue"].put_nowait(f"ERROR: {e}")

def _run_check(job_id, mode, loop):
    try:
        from checking_url.runner import run as check_run
        import os
        conc = int(os.getenv("CHECK_CONCURRENCY", 20))
        with capture_prints(job_id, loop):
            fut = asyncio.run_coroutine_threadsafe(
                check_run(concurrency=conc, limit=0, mode=mode), loop
            )
            set_job_future(job_id, fut)
            fut.result(timeout=7200)
        finish_job(job_id, "done")
    except asyncio.CancelledError:
        finish_job(job_id, "stopped")
    except Exception as e:
        job = get_job(job_id)
        if job and job.get("status") == "stopped":
            finish_job(job_id, "stopped")
        else:
            finish_job(job_id, "failed")
            if job:
                job["queue"].put_nowait(f"ERROR: {e}")

def _run_export(job_id, fmt, batch_size, loop):
    try:
        from capture_url.runner import run as capture_run
        from capture_url.excel_exporter import export_capture_workbook
        from capture_url.docx_report_generator import build_report_from_mongo
        from datetime import datetime, timezone, timedelta
        import os
        IST = timezone(timedelta(hours=5, minutes=30))
        run_ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join("output", run_ts)
        conc = int(os.getenv("SCREENSHOT_CONCURRENCY", 15))
        with capture_prints(job_id, loop):
            fut = asyncio.run_coroutine_threadsafe(
                capture_run(concurrency=conc, limit=0), loop
            )
            set_job_future(job_id, fut)
            ids = fut.result(timeout=7200)
        if ids:
            if fmt == 3:
                from main import _size_based_split
                batches = _size_based_split(ids)
                for i, batch_ids in enumerate(batches, 1):
                    bd = os.path.join(run_dir, f"batch_{i:03d}")
                    build_report_from_mongo(domain_ids=batch_ids, output_dir=bd, batch_size=0, cleanup=False, pdf=True, single_file=True)
                    export_capture_workbook(domain_ids=batch_ids, output_dir=bd, batch_size=0, single_file=True)
            elif fmt == 2:
                export_capture_workbook(domain_ids=ids, output_dir=run_dir, batch_size=batch_size)
                build_report_from_mongo(domain_ids=ids, output_dir=run_dir, batch_size=batch_size, cleanup=False, pdf=True)
            else:
                export_capture_workbook(domain_ids=ids, output_dir=run_dir, batch_size=0, single_file=True)
                build_report_from_mongo(domain_ids=ids, output_dir=run_dir, batch_size=0, cleanup=False, pdf=True, single_file=True)
        finish_job(job_id, "done")
    except asyncio.CancelledError:
        finish_job(job_id, "stopped")
    except Exception as e:
        job = get_job(job_id)
        if job and job.get("status") == "stopped":
            finish_job(job_id, "stopped")
        else:
            finish_job(job_id, "failed")
            if job:
                job["queue"].put_nowait(f"ERROR: {e}")

def _run_screenshot(job_id, domains_list, loop):
    try:
        from capture_url.screenshot_runner import run as ss_run
        import os
        conc = int(os.getenv("SCREENSHOT_CONCURRENCY", 15))
        ids = domains_list if domains_list else None
        with capture_prints(job_id, loop):
            fut = asyncio.run_coroutine_threadsafe(
                ss_run(domain_ids=ids, concurrency=conc), loop
            )
            set_job_future(job_id, fut)
            fut.result(timeout=7200)
        finish_job(job_id, "done")
    except asyncio.CancelledError:
        finish_job(job_id, "stopped")
    except Exception as e:
        job = get_job(job_id)
        if job and job.get("status") == "stopped":
            finish_job(job_id, "stopped")
        else:
            finish_job(job_id, "failed")
            if job:
                job["queue"].put_nowait(f"ERROR: {e}")


@router.post("/run/keywords")
def run_keywords(bg: BackgroundTasks):
    if is_stage_running("keywords"):
        raise HTTPException(409, "keywords stage already running")
    job_id = new_job("keywords")
    loop = _get_loop()
    bg.add_task(_run_keywords, job_id, loop)
    return {"job_id": job_id}

@router.post("/run/check")
def run_check(body: CheckBody, bg: BackgroundTasks):
    if is_stage_running("check"):
        raise HTTPException(409, "check stage already running")
    job_id = new_job("check")
    loop = _get_loop()
    bg.add_task(_run_check, job_id, body.mode, loop)
    return {"job_id": job_id}

@router.post("/run/export")
def run_export(body: ExportBody, bg: BackgroundTasks):
    if is_stage_running("export"):
        raise HTTPException(409, "export stage already running")
    job_id = new_job("export")
    loop = _get_loop()
    bg.add_task(_run_export, job_id, body.format, body.batch_size, loop)
    return {"job_id": job_id}

@router.post("/run/screenshot")
def run_screenshot(body: ScreenshotBody, bg: BackgroundTasks):
    if is_stage_running("screenshot"):
        raise HTTPException(409, "screenshot stage already running")
    job_id = new_job("screenshot")
    loop = _get_loop()
    domains = body.domains if body.mode == "file" else []
    bg.add_task(_run_screenshot, job_id, domains, loop)
    return {"job_id": job_id}

@router.post("/stop/{stage}")
def stop_stage_endpoint(stage: str):
    stopped = stop_stage(stage)
    return {"stage": stage, "stopped": stopped}

@router.post("/stop")
def stop_all_endpoint():
    count = stop_all()
    return {"stopped_count": count}

@router.post("/jobs/{job_id}/stop")
def stop_job_endpoint(job_id: str):
    stopped = stop_job(job_id)
    if not stopped:
        raise HTTPException(404, "job not found or already completed")
    return {"job_id": job_id, "status": "stopped"}

@router.post("/run/import")
def run_import(body: ImportBody):
    if not body.domains:
        raise HTTPException(400, "domains list is empty")
    from db.mongo_client import checked_domains, extract_domain
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(IST).strftime("%Y-%m-%d")
    inserted = reset = 0
    for raw in body.domains:
        d = raw.strip().replace("https://","").replace("http://","").lstrip("www.").rstrip("/").lower()
        d = extract_domain(f"https://{d}") or d
        if not d or "." not in d:
            continue
        existing = checked_domains().find_one({"_id": d}, {"_id": 1})
        if existing:
            checked_domains().update_one({"_id": d}, {"$set": {"screenshot_taken": False, "exported": False, "screenshot_failed_reason": None}})
            reset += 1
        else:
            checked_domains().update_one({"_id": d},
                {"$set": {"domain": d, "url": f"https://{d}", "status": "gambling",
                    "reason": "Manual true positive import", "screenshot_taken": False,
                    "screenshot_failed_reason": None, "source": "manual_import"},
                 "$setOnInsert": {"added_date": today}}, upsert=True)
            inserted += 1
    return {"inserted": inserted, "reset": reset}

@router.get("/status")
def status():
    return all_jobs()

@router.get("/logs/{job_id}")
async def logs(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    async def generator():
        q = job["queue"]
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=30)
                if msg is None:
                    yield "data: __DONE__\n\n"
                    break
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                yield "data: __PING__\n\n"
    return StreamingResponse(generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
