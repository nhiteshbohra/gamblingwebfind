"""
api/main.py -- FastAPI entry point.
Mounts web/ as static files and registers all routers.
Start with: uvicorn api.main:app --reload --port 8081
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from contextlib import asynccontextmanager
from api.routers import pipeline, domains, reports, settings as settings_router, stats as stats_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # ponytail: Cleanly terminate any active background subprocesses on server shutdown / reload
    try:
        from api.jobs import stop_all
        stop_all()
    except Exception:
        pass


app = FastAPI(title="GamblingWebFind API", version="1.0", lifespan=lifespan)

app.include_router(pipeline.router)
app.include_router(domains.router)
app.include_router(reports.router)
app.include_router(settings_router.router)
app.include_router(stats_router.router)

# Serve web/ as static files; index.html is the SPA shell
_web_dir = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=str(_web_dir)), name="static")

@app.get("/")
def index():
    return FileResponse(str(_web_dir / "index.html"))
