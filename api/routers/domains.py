from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from db.mongo_client import checked_domains as _cd
from export_domains.screenshot import _url_to_filename, is_valid_screenshot
import os

router = APIRouter(prefix="/api")
SCREENSHOTS_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


@router.get("/domains")
def list_domains(
    status: str = None,
    q: str = None,
    screenshot: str = None,
    exported: str = None,
    source: str = None,
    from_date: str = None,
    to_date: str = None,
    page: int = 1,
    per_page: int = 50,
):
    flt = {}
    if status and status != "all":
        flt["status"] = status
    if q:
        flt["_id"] = {"$regex": q, "$options": "i"}
    if screenshot == "taken":
        flt["screenshot_taken"] = True
    elif screenshot == "pending":
        flt["screenshot_taken"] = False
    if exported == "true":
        flt["exported"] = True
    elif exported == "false":
        flt["exported"] = {"$ne": True}
    if source:
        flt["source"] = source
    if from_date or to_date:
        flt["added_date"] = {}
        if from_date:
            flt["added_date"]["$gte"] = from_date
        if to_date:
            flt["added_date"]["$lte"] = to_date

    total = _cd().count_documents(flt)
    skip = (page - 1) * per_page
    docs = list(_cd().find(flt).sort("added_date", -1).skip(skip).limit(per_page))
    screenshots_dir = os.getenv("SCREENSHOT_DIR", SCREENSHOTS_DIR)
    results = []
    for d in docs:
        domain = d.get("domain") or d.get("_id")
        url = d.get("url") or f"https://{domain}"
        has_shot = False
        for cand in [_url_to_filename(url), _url_to_filename(f"https://{domain}"), _url_to_filename(f"http://{domain}")]:
            p = os.path.join(screenshots_dir, cand)
            if os.path.exists(p) and is_valid_screenshot(p):
                has_shot = True
                break
        results.append({
            "_id": d.get("_id"),
            "domain": domain,
            "url": url,
            "status": d.get("status"),
            "reason": d.get("reason"),
            "screenshot_taken": d.get("screenshot_taken", False),
            "screenshot_failed_reason": d.get("screenshot_failed_reason"),
            "has_screenshot_file": has_shot,
            "added_date": d.get("added_date"),
            "exported": d.get("exported", False),
            "exported_at": d.get("exported_at"),
            "source": d.get("source"),
        })
    return {"total": total, "page": page, "per_page": per_page, "results": results}


@router.get("/domains/{domain}")
def get_domain(domain: str):
    d = _cd().find_one({"_id": domain})
    if not d:
        raise HTTPException(404, "domain not found")
    d["_id"] = str(d["_id"])
    return d


@router.get("/domains/{domain}/screenshot")
def get_screenshot(domain: str):
    d = _cd().find_one({"_id": domain}, {"url": 1})
    if not d:
        raise HTTPException(404, "domain not found")
    url = d.get("url") or f"https://{domain}"
    screenshots_dir = os.getenv("SCREENSHOT_DIR", SCREENSHOTS_DIR)
    for cand in [_url_to_filename(url), _url_to_filename(f"https://{domain}"), _url_to_filename(f"http://{domain}")]:
        p = os.path.join(screenshots_dir, cand)
        if os.path.exists(p) and is_valid_screenshot(p):
            return FileResponse(p, media_type="image/jpeg")
    raise HTTPException(404, "screenshot not found")
