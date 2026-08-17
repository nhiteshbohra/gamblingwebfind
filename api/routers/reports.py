from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import os
from pathlib import Path

router = APIRouter(prefix="/api")
OUTPUT_DIR = "output"

@router.get("/reports")
def list_reports():
    if not os.path.exists(OUTPUT_DIR):
        return []
    results = []
    for root, dirs, files in os.walk(OUTPUT_DIR):
        dirs[:] = [d for d in sorted(dirs) if d != "screenshots"]
        for fname in sorted(files):
            if fname.endswith((".pdf", ".xlsx", ".docx")):
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, OUTPUT_DIR).replace("\\", "/")
                stat = os.stat(fpath)
                results.append({
                    "path": rel,
                    "name": fname,
                    "size_mb": round(stat.st_size / 1024 / 1024, 2),
                    "modified": int(stat.st_mtime),
                    "ext": fname.rsplit(".", 1)[-1],
                })
    return results

@router.get("/reports/download/{path:path}")
def download_report(path: str):
    fpath = os.path.join(OUTPUT_DIR, path)
    if not os.path.exists(fpath) or not os.path.isfile(fpath):
        raise HTTPException(404, "file not found")
    ext = fpath.rsplit(".", 1)[-1].lower()
    types = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    return FileResponse(fpath, media_type=types.get(ext, "application/octet-stream"),
                        filename=os.path.basename(fpath))
