import os
import re
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from db.mongo_client import checked_domains as _cd, source_domains as _sd, resolve_ip, write_result
from export_domains.screenshot import find_screenshot_path

router = APIRouter(prefix="/api")
SCREENSHOTS_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


class TestUrlRequest(BaseModel):
    url: str
    run_ai: bool = True
    take_screenshot: bool = True


class SaveDomainRequest(BaseModel):
    domain: str
    url: Optional[str] = None
    status: str
    reason: str
    ip: Optional[Any] = None
    screenshot_taken: Optional[bool] = False


@router.get("/domains")
def list_domains(
    status: str = None,
    q: str = None,
    ip: str = None,
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
    if ip:
        # Match string or array element in ip field
        clean_ip = ip.strip()
        flt["$or"] = [
            {"ip": {"$regex": clean_ip, "$options": "i"}},
            {"ip": clean_ip}
        ]
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
        has_shot = find_screenshot_path(url, domain, screenshots_dir) is not None
        results.append({
            "_id": d.get("_id"),
            "domain": domain,
            "url": url,
            "status": d.get("status"),
            "reason": d.get("reason"),
            "screenshot_taken": d.get("screenshot_taken", False),
            "screenshot_failed_reason": d.get("screenshot_failed_reason"),
            "screenshot_date": d.get("screenshot_date"),
            "has_screenshot_file": has_shot,
            "added_date": d.get("added_date"),
            "ip": d.get("ip"),
            "asn": d.get("asn"),
            "confidence": d.get("confidence"),
            "category": d.get("category"),
            "decided_by": d.get("decided_by"),
            "matched_keywords": d.get("matched_keywords"),
            "exported": d.get("exported", False),
            "exported_at": d.get("exported_at"),
            "source": d.get("source"),
        })
    return {"total": total, "page": page, "per_page": per_page, "results": results}


@router.post("/test-url")
async def test_single_url(req: TestUrlRequest):
    """
    Interactive Sandbox Tester:
    Fetches a single URL live, runs heuristic keyword screening,
    evaluates with Ollama AI challenge (if enabled), resolves IP,
    and optionally captures a screenshot.
    """
    raw_url = req.url.strip()
    if not raw_url:
        raise HTTPException(400, "URL cannot be empty")

    if not raw_url.startswith(("http://", "https://")):
        raw_url = f"https://{raw_url}"

    import tldextract
    import time

    ext = tldextract.extract(raw_url)
    domain = (ext.registered_domain or ext.domain).lower()
    if not domain or "." not in domain:
        domain = raw_url.split("/")[2].split(":")[0].lower()

    # 1. Resolve DNS / IP
    resolved_ip = resolve_ip(domain)

    # 2. Fetch live webpage HTML
    start_time = time.time()
    html_content = ""
    http_status = None
    fetch_err = None
    latency = 0.0

    try:
        from checking_url.fetcher import fetch
        fetch_res = await fetch(raw_url, domain, timeout_seconds=15, per_domain_delay=0.0)
        http_status = fetch_res.status_code
        html_content = fetch_res.html or ""
        latency = round(fetch_res.latency or (time.time() - start_time), 2)
        if fetch_res.error:
            fetch_err = str(fetch_res.error)
        elif fetch_res.failure_type:
            fetch_err = fetch_res.failure_type
    except Exception as e:
        fetch_err = str(e)
        latency = round(time.time() - start_time, 2)

    # 3. Heuristic Pre-classification
    heuristic_verdict = "unconfirmed"
    heuristic_score = 0.0
    matched_keywords = []
    negative_signals = []
    reason_summary = "Live inspection"

    try:
        from checking_url.classifier import (
            classify, load_keywords, WEAK_GAMBLING_SIGNALS,
            detect_negative_archetype, _extract_text
        )
        from checking_url.ai_classifier import translate_to_english_if_needed
        kw_set = load_keywords()
        classify_html = html_content  # safe fallback if translation raises before reassigning
        if html_content:
            classify_html, _ = await translate_to_english_if_needed(html_content, url=raw_url)
            heuristic_verdict, matched_keywords = classify(classify_html, url=raw_url, keywords=kw_set)
            heuristic_score = sum(0.5 if kw in WEAK_GAMBLING_SIGNALS else 1.0 for kw in matched_keywords)
            text_content = _extract_text(classify_html)
            is_neg, neg_reason = detect_negative_archetype(text_content)
            if is_neg and neg_reason:
                negative_signals = [neg_reason]
            reason_summary = f"{len(matched_keywords)} keywords matched (score: {heuristic_score:.1f})"
        else:
            if fetch_err == "blocked" or http_status in (403, 429):
                heuristic_verdict = "blocked"
                reason_summary = "Blocked: Cloudflare WAF / HTTP 403"
            elif fetch_err == "parked":
                # Parked/for-sale registrar lander — reachable, not gambling, not dead.
                heuristic_verdict = "regular"
                reason_summary = "Regular: Parked/For-Sale domain lander detected"
            else:
                heuristic_verdict = "dead"
                reason_summary = f"Dead: {fetch_err or 'Host unreachable'}"
    except Exception as e:
        reason_summary = f"Heuristic error: {e}"

    # 4. Ollama AI Evaluation (if enabled and page content exists)
    ai_result = None
    final_verdict = heuristic_verdict

    if req.run_ai and html_content:
        try:
            from checking_url.ai_classifier import classify_with_challenge
            ai_eval = await classify_with_challenge(
                html=classify_html,
                url=raw_url,
                matched_keywords=matched_keywords,
                fast_mode=False
            )
            ai_result = ai_eval
            if ai_eval.get("verdict") in ("gambling", "regular", "blocked", "dead"):
                final_verdict = ai_eval.get("verdict")
                reason_summary = ai_eval.get("reason") or reason_summary
        except Exception as e:
            ai_result = {"error": f"Ollama AI offline or timeout: {e}"}

    if final_verdict in ("gambling", "regular"):
        pass
    elif http_status in (403, 429) or (html_content and "cf-challenge" in html_content.lower()):
        final_verdict = "blocked"
    elif http_status == 404 or (not html_content and fetch_err):
        final_verdict = "dead"

    # 5. Screenshot capture if requested
    screenshot_taken = False
    screenshot_url = None
    if req.take_screenshot:
        try:
            from export_domains.screenshot import BrowserPool
            pool = BrowserPool(concurrency=1)
            await pool.start()
            screenshots_dir = os.getenv("SCREENSHOT_DIR", SCREENSHOTS_DIR)
            os.makedirs(screenshots_dir, exist_ok=True)
            shot_path, shot_status, _ = await pool.capture_url(raw_url, screenshots_dir, domain=domain)
            await pool.close()
            if shot_path and os.path.exists(shot_path):
                screenshot_taken = True
                screenshot_url = f"/api/domains/{domain}/screenshot?t={int(time.time())}"
        except Exception as e:
            pass

    return {
        "domain": domain,
        "url": raw_url,
        "ip": resolved_ip,
        "http_status": http_status,
        "latency_sec": latency,
        "fetch_error": fetch_err,
        "status": final_verdict,
        "reason": reason_summary,
        "heuristic": {
            "score": heuristic_score,
            "matched_keywords": matched_keywords,
            "negative_signals": negative_signals,
            "pre_verdict": heuristic_verdict
        },
        "ai": ai_result,
        "screenshot_taken": screenshot_taken,
        "screenshot_url": screenshot_url,
        "html_snippet": html_content[:600] if html_content else None
    }


@router.post("/save-domain")
def save_tested_domain(req: SaveDomainRequest):
    """Save/upsert a tested domain directly into checked_domains & domain_Listed."""
    try:
        write_result(
            domain=req.domain,
            status=req.status,
            reason=req.reason,
            url=req.url,
            screenshot_taken=req.screenshot_taken,
            ip=req.ip
        )
        return {"status": "success", "message": f"Successfully saved {req.domain} as '{req.status}' in MongoDB."}
    except Exception as e:
        raise HTTPException(500, f"Failed to save domain: {e}")


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
    url = (d.get("url") if d else None) or f"https://{domain}"
    screenshots_dir = os.getenv("SCREENSHOT_DIR", SCREENSHOTS_DIR)
    p = find_screenshot_path(url, domain, screenshots_dir)
    if p:
        return FileResponse(p, media_type="image/jpeg")
    raise HTTPException(404, "screenshot not found")


_CSP_OR_FRAME_META = re.compile(
    r'<meta[^>]+http-equiv=["\']?(?:content-security-policy|x-frame-options)["\']?[^>]*>',
    re.IGNORECASE,
)
_HEAD_OPEN_TAG = re.compile(r'<head[^>]*>', re.IGNORECASE)


@router.get("/domains/{domain}/proxy", response_class=HTMLResponse)
async def proxy_domain_page(domain: str):
    """Server-side fetch so the dashboard can embed this domain inline even when the site
    sends X-Frame-Options/CSP headers blocking iframe embedding -- those only govern the
    ORIGINAL response; this serves our own fresh response instead, which sets neither.

    Only ever fetches the URL already on file for a known domain record in MongoDB, never
    an arbitrary caller-supplied URL -- keeps this from becoming an open SSRF relay.

    A <base href> is injected so relative CSS/JS/image URLs resolve against the real site
    and load directly from it (X-Frame-Options only restricts document-level framing, not
    sub-resource loads, so this doesn't need to proxy every asset too). The page's own
    CSP/X-Frame-Options <meta> tags are stripped so they can't re-block rendering inside
    our copy. The iframe embedding this response MUST use sandbox="allow-scripts" (script
    execution allowed) WITHOUT "allow-same-origin" -- since this response is served from
    OUR OWN origin, combining the two would let the target's script read this dashboard's
    cookies/localStorage and call its APIs. Without allow-same-origin the frame gets an
    opaque, isolated origin instead, so scripts run but can't touch anything of ours.
    """
    d = _cd().find_one({"_id": domain}, {"url": 1})
    if not d:
        raise HTTPException(404, "domain not found")
    url = d.get("url") or f"https://{domain}"

    from checking_url.fetcher import fetch
    result = await fetch(url, domain, timeout_seconds=15, per_domain_delay=0.0)
    if not result.html:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:40px;color:#888;background:#111'>"
            f"Could not load {domain} ({result.failure_type or 'no response'}).</body></html>",
            status_code=502,
        )

    html = _CSP_OR_FRAME_META.sub("", result.html)
    base_url = result.final_url or url
    base_tag = f'<base href="{base_url}">'
    html = _HEAD_OPEN_TAG.sub(lambda m: m.group(0) + base_tag, html, count=1) if _HEAD_OPEN_TAG.search(html) else base_tag + html

    resp = HTMLResponse(html)
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"  # only our own dashboard may frame this
    return resp


@router.post("/backup")
def trigger_backup():
    try:
        from db.mongo_client import backup_databases
        res = backup_databases()
        return res
    except Exception as e:
        raise HTTPException(500, f"Database backup failed: {e}")


