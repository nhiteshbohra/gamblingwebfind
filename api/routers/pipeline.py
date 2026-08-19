from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Literal
import asyncio, json, os, sys, subprocess
from pathlib import Path
from api.jobs import (
    new_job, get_job, all_jobs, finish_job, is_stage_running,
    set_job_process, stop_stage, stop_job, stop_all
)

router = APIRouter(prefix="/api")

class CheckBody(BaseModel):
    mode: Literal["new", "blocked", "unconfirmed", "regular", "dead"] = "new"

class ExportBody(BaseModel):
    format: int = 1
    batch_size: int = 40

class ImportBody(BaseModel):
    domains: list = []

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_subprocess_worker(job_id: str, cmd: list[str], loop: asyncio.AbstractEventLoop):
    job = get_job(job_id)
    if not job:
        return
    q = job["queue"]

    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            env=env,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        set_job_process(job_id, proc)

        for line in iter(proc.stdout.readline, ''):
            if line:
                clean_line = line.rstrip("\r\n")
                loop.call_soon_threadsafe(q.put_nowait, clean_line)

        proc.stdout.close()
        proc.wait()
        if job["status"] == "running":
            loop.call_soon_threadsafe(finish_job, job_id, "done" if proc.returncode == 0 else "failed")
    except Exception as e:
        if job["status"] == "running":
            loop.call_soon_threadsafe(q.put_nowait, f"[!] Pipeline process error: {e}")
            loop.call_soon_threadsafe(finish_job, job_id, "failed")


@router.post("/run/keywords")
async def run_keywords(bg: BackgroundTasks):
    if is_stage_running("keywords"):
        raise HTTPException(409, "keywords stage already running")
    job_id = new_job("keywords")
    cmd = [sys.executable, "-u", str(PROJECT_ROOT / "keywordssearch" / "searxng_search.py")]
    loop = asyncio.get_running_loop()
    bg.add_task(_run_subprocess_worker, job_id, cmd, loop)
    return {"job_id": job_id}


@router.post("/run/check")
async def run_check(body: CheckBody, bg: BackgroundTasks):
    if is_stage_running("check"):
        raise HTTPException(409, "check stage already running")
    job_id = new_job("check")
    cmd = [sys.executable, "-u", str(PROJECT_ROOT / "checking_url" / "runner.py"), "--mode", body.mode]
    loop = asyncio.get_running_loop()
    bg.add_task(_run_subprocess_worker, job_id, cmd, loop)
    return {"job_id": job_id}


@router.post("/run/export")
async def run_export(body: ExportBody, bg: BackgroundTasks):
    if is_stage_running("export"):
        raise HTTPException(409, "export stage already running")
    job_id = new_job("export")
    cmd = [sys.executable, "-u", str(PROJECT_ROOT / "export_domains" / "exporter.py")]
    loop = asyncio.get_running_loop()
    bg.add_task(_run_subprocess_worker, job_id, cmd, loop)
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
