from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal
import asyncio, json, os, sys
from pathlib import Path
from api.jobs import (
    new_job, get_job, all_jobs, finish_job, is_stage_running,
    capture_prints, set_job_future, stop_stage, stop_job, stop_all
)

router = APIRouter(prefix="/api")

class CheckBody(BaseModel):
    mode: Literal["new", "blocked", "unconfirmed", "regular", "dead"] = "new"

class ExportBody(BaseModel):
    format: int = 1
    batch_size: int = 40

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
        root_dir = Path(__file__).resolve().parent.parent.parent
        cands = list(root_dir.glob("gambling_top_*_keywords.json")) + list(root_dir.glob("*keyword*.json"))
        kw_path = cands[0] if cands else (root_dir / "gambling_top_944_keywords.json")
        with open(kw_path, encoding="utf-8") as f:
            keywords = [str(k).strip().lower() for k in json.load(f) if str(k).strip()]
        with capture_prints(job_id, loop):
            fut = asyncio.run_coroutine_threadsafe(run_search(keywords), loop)
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
        from export_domains.exporter import run as export_run
        with capture_prints(job_id, loop):
            fut = asyncio.run_coroutine_threadsafe(
                export_run(limit=0), loop
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
    from db.mongo_client import ingest_true_positives
    inserted, reset = ingest_true_positives(body.domains)
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
