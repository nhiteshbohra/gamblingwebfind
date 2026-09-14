from fastapi import APIRouter
import os
import os
from db.mongo_client import get_db

router = APIRouter(prefix="/api")

@router.get("/settings")
def get_settings():
    # Mongo check
    db = None
    try:
        db = get_db()
        db.command("ping")
        mongo_ok = True
        mongo_msg = "Connected"
    except Exception as e:
        mongo_ok = False
        mongo_msg = str(e)[:80]

    # OmniRoute check
    import urllib.request
    omniroute_ok = False
    omniroute_url = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
    omniroute_key = os.getenv("OMNIROUTE_API_KEY", "")
    try:
        headers = {"Authorization": f"Bearer {omniroute_key}"} if omniroute_key else {}
        req = urllib.request.Request(f"{omniroute_url}/models", headers=headers)
        res = urllib.request.urlopen(req, timeout=3)
        if res.status == 200:
            omniroute_ok = True
    except Exception:
        pass

    return {
        "mongo": {"ok": mongo_ok, "message": mongo_msg, "db": db.name if db is not None else os.getenv("MONGO_DB_NAME", "")},
        "omniroute": {"ok": omniroute_ok, "url": omniroute_url, "model": os.getenv("OMNIROUTE_MODEL", "auto")},
        "ollama": {"ok": omniroute_ok, "url": omniroute_url},  # alias for backward-compatible frontend
        "config": {
            "screenshot_dir": os.getenv("SCREENSHOT_DIR", "output/screenshots"),
            "check_concurrency": os.getenv("CHECK_CONCURRENCY", "20"),
            "screenshot_concurrency": os.getenv("SCREENSHOT_CONCURRENCY", "15"),
            "export_batch_size": os.getenv("EXPORT_BATCH_SIZE", "40"),
        }
    }
