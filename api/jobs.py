import asyncio, sys, io, uuid
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

IST = timezone(timedelta(hours=5, minutes=30))
_jobs = {}

def new_job(stage):
    job_id = uuid.uuid4().hex[:10]
    _jobs[job_id] = {
        "job_id": job_id,
        "stage": stage,
        "status": "running",
        "started_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        "queue": asyncio.Queue(),
        "future": None,
        "stop_requested": False,
    }
    return job_id

def set_job_future(job_id, fut):
    job = _jobs.get(job_id)
    if job:
        job["future"] = fut

def get_job(job_id):
    return _jobs.get(job_id)

def all_jobs():
    return [{k: v for k, v in j.items() if k not in ("queue", "future")} for j in _jobs.values()]

def finish_job(job_id, status="done"):
    job = _jobs.get(job_id)
    if job:
        if job["status"] == "running":
            job["status"] = status
        job["queue"].put_nowait(None)

def stop_job(job_id):
    job = _jobs.get(job_id)
    if not job:
        return False
    if job["status"] == "running":
        job["status"] = "stopped"
        job["stop_requested"] = True
        if job.get("future"):
            try:
                job["future"].cancel()
            except Exception:
                pass
        try:
            job["queue"].put_nowait("[!] Pipeline stage stopped by user.")
            job["queue"].put_nowait(None)
        except Exception:
            pass
        return True
    return False

def stop_stage(stage):
    stopped_any = False
    for job_id, job in list(_jobs.items()):
        if job.get("stage") == stage and job.get("status") == "running":
            if stop_job(job_id):
                stopped_any = True
    return stopped_any

def stop_all():
    stopped_count = 0
    for job_id, job in list(_jobs.items()):
        if job.get("status") == "running":
            if stop_job(job_id):
                stopped_count += 1
    return stopped_count

def is_stage_running(stage):
    return any(j.get("stage") == stage and j.get("status") == "running" for j in _jobs.values())

async def log(job_id, msg):
    job = _jobs.get(job_id)
    if job:
        await job["queue"].put(msg)

@contextmanager
def capture_prints(job_id, loop):
    class _W(io.TextIOBase):
        def write(self, s):
            s = s.strip()
            if s:
                asyncio.run_coroutine_threadsafe(log(job_id, s), loop)
            return len(s)
        def flush(self): pass
    old = sys.stdout
    sys.stdout = _W()
    try:
        yield
    finally:
        sys.stdout = old
