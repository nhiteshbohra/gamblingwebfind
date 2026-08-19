import asyncio
import os
import sys
import uuid
import subprocess
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
_jobs = {}


def new_job(stage: str) -> str:
    job_id = uuid.uuid4().hex[:10]
    _jobs[job_id] = {
        "job_id": job_id,
        "stage": stage,
        "status": "running",
        "started_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "queue": asyncio.Queue(),
        "process": None,
    }
    return job_id


def set_job_process(job_id: str, proc):
    job = _jobs.get(job_id)
    if job:
        job["process"] = proc


def get_job(job_id: str):
    return _jobs.get(job_id)


def all_jobs():
    return [{k: v for k, v in j.items() if k not in ("queue", "process")} for j in _jobs.values()]


def finish_job(job_id: str, status: str = "done"):
    job = _jobs.get(job_id)
    if job:
        if job["status"] == "running":
            job["status"] = status
        job["queue"].put_nowait(None)


def stop_job(job_id: str) -> bool:
    job = _jobs.get(job_id)
    if not job:
        return False
    if job["status"] == "running":
        job["status"] = "stopped"
        proc = job.get("process")
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
            if sys.platform == "win32" and proc.pid:
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
                except Exception:
                    pass
        try:
            job["queue"].put_nowait("[!] Pipeline stage stopped by user.")
            job["queue"].put_nowait(None)
        except Exception:
            pass
        return True
    return False


def stop_stage(stage: str) -> bool:
    stopped_any = False
    for job_id, job in list(_jobs.items()):
        if job.get("stage") == stage and job.get("status") == "running":
            if stop_job(job_id):
                stopped_any = True
    return stopped_any


def stop_all() -> int:
    stopped_count = 0
    for job_id, job in list(_jobs.items()):
        if job.get("status") == "running":
            if stop_job(job_id):
                stopped_count += 1
    return stopped_count


def is_stage_running(stage: str) -> bool:
    return any(j.get("stage") == stage and j.get("status") == "running" for j in _jobs.values())


async def log(job_id: str, msg: str):
    job = _jobs.get(job_id)
    if job:
        await job["queue"].put(msg)
