from fastapi import APIRouter
import os, asyncio
from db.mongo_client import get_db

router = APIRouter(prefix="/api")

@router.get("/settings")
def get_settings():
    # Mongo check
    try:
        get_db().command("ping")
        mongo_ok = True
        mongo_msg = "Connected"
    except Exception as e:
        mongo_ok = False
        mongo_msg = str(e)[:80]

    # Ollama check
    import urllib.request
    ollama_ok = False
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    try:
        urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=3)
        ollama_ok = True
    except Exception:
        pass

    return {
        "mongo": {"ok": mongo_ok, "message": mongo_msg, "db": os.getenv("MONGO_DB_NAME", "gamblingsites")},
        "ollama": {"ok": ollama_ok, "url": ollama_url},
        "config": {
            "screenshot_dir": os.getenv("SCREENSHOT_DIR", "output/screenshots"),
            "check_concurrency": os.getenv("CHECK_CONCURRENCY", "20"),
            "screenshot_concurrency": os.getenv("SCREENSHOT_CONCURRENCY", "15"),
            "export_batch_size": os.getenv("EXPORT_BATCH_SIZE", "40"),
        }
    }
