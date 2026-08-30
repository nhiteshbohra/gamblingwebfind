"""
checking_url/runner.py — Triple-Lock Async Classifier + Immediate Screenshot Capture.

New Architecture:
1. Fast Fetch via Scrapling AsyncFetcher (curl_cffi TLS impersonation).
2. Layer 2: Heuristic Pre-Screen (988 Keywords, weighted float scoring).
   NOTE: the exact thresholds/gating live in checking_url/classifier.py's own
   module docstring -- treat that file as the single source of truth rather
   than duplicating numbers here, since this summary previously drifted out
   of sync with the real code (it said 5.0/2.5, the real gates are 4.0/1.5
   plus additional strong/actionable-signal conditions). As of 2026-08-24,
   summarized from classifier.py:
   - Score >= 4.0 AND a strong/actionable signal present -> gambling (locked)
   - Any strong signal at all (any score)                -> needs_ai
   - Hospitality/negative-archetype page, score < 1.5,
     no hard actionable signal                            -> regular
   - Everything else                                       -> regular
   - Hospitality/educational/e-commerce gatekeeping prevents false locks
3. Layer 3: Ollama AI 2-Round Challenge System:
   - Round 1: Standard classify_with_challenge()
   - Round 2: If Round 1 says 'regular' -> Challenge: AI must prove with specific evidence
   - If evidence vague/low confidence -> override to gambling
   - If Ollama offline + keywords matched -> unconfirmed (re-queued for later)
4. Immediate Screenshot: If status is 'gambling', Playwright BrowserPool captures immediately.
5. MongoDB sync: updates checked_domains and domain_Listed.
"""
import asyncio
import logging
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in sys.path
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
from tqdm import tqdm

# Silence verbose third-party loggers to keep progress bar clean
for _logger_name in ("scrapling", "curl_cffi", "urllib3", "asyncio", "playwright"):
    _lg = logging.getLogger(_logger_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.handlers.clear()
    _lg.addHandler(logging.NullHandler())

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

from db.mongo_client import (
    find_active_domains,
    find_blocked_domains,
    find_unconfirmed_domains,
    find_regular_domains,
    find_dead_domains,
    find_for_sale_domains,
    find_unreviewed_gambling_domains,
    get_gambling_asn_concentration,
    resolve_ip,
    resolve_asn,
    write_result,
    get_db,
)
from checking_url.fetcher import fetch, check_cloaking
from checking_url.classifier import load_keywords, classify, is_gambling_domain, is_parked_or_for_sale, _extract_text
from checking_url.ai_classifier import classify_with_challenge, close_ai_session, _timeout_mgr, classify_screenshot_vision, clean_page_text, translate_to_english_if_needed
from checking_url.ocr_extractor import extract_ocr_text
from export_domains.screenshot import BrowserPool, is_valid_screenshot, delete_screenshot, all_filename_candidates

OUTPUT_SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", os.path.join("output", "screenshots"))


async def async_write_result(*args, **kwargs):
    return await asyncio.to_thread(write_result, *args, **kwargs)


async def run(concurrency: int = None, limit: int = 0, mode: str = "new", min_age_days: int = 0, full_recheck: bool = False):
    get_db()
    output_screenshot_dir = os.getenv("SCREENSHOT_DIR", OUTPUT_SCREENSHOT_DIR)
    os.makedirs(output_screenshot_dir, exist_ok=True)
    concurrency = concurrency or int(os.getenv("MAX_CONCURRENT_FETCHES", 20))
    timeout = float(os.getenv("FETCH_TIMEOUT", 10))
    delay = float(os.getenv("PER_DOMAIN_DELAY", 2.0))

    keywords = load_keywords()

    # Reset the dynamic timeout EMA so a prior crashed/saturated run's inflated
    # timeouts don't carry over and slow down this fresh batch.
    await _timeout_mgr.reset_ema()

    # Hosting-cluster corroboration: build the ASN concentration table once per run (not
    # per-domain — this is a full collection aggregation) from everything already
    # classified. Used only as a soft corroborating hint for already-ambiguous (needs_ai)
    # domains — see get_gambling_asn_concentration() for why this never auto-locks a verdict.
    asn_concentration = await asyncio.to_thread(get_gambling_asn_concentration)
    if asn_concentration:
        print(f"[check] Loaded {len(asn_concentration)} gambling-concentrated hosting ASNs for corroboration.")

    if mode == "blocked":
        pending = list(find_blocked_domains(limit=limit))
        desc_label = "Rechecking Blocked"
    elif mode == "unconfirmed":
        pending = list(find_unconfirmed_domains(limit=limit))
        desc_label = "Rechecking Unconfirmed"
    elif mode == "regular":
        pending = list(find_regular_domains(limit=limit, min_age_days=min_age_days))
        desc_label = "Rechecking Regular"
    elif mode == "dead":
        pending = list(find_dead_domains(limit=limit, min_age_days=min_age_days))
        desc_label = "Rechecking Dead"
    elif mode == "for_sale":
        pending = list(find_for_sale_domains(limit=limit))
        desc_label = "Rechecking For Sale"
    elif mode == "gambling":
        pending = list(find_unreviewed_gambling_domains(limit=limit))
        desc_label = "Rechecking Unreviewed Gambling"
    else:
        pending = list(find_active_domains(limit=limit))
        desc_label = "Checking & Capturing"

    if not pending:
        target_name = mode if mode in ("blocked", "unconfirmed", "regular", "dead", "gambling") else "active unprocessed"
        print(f"[check] No {target_name} domains to process.")
        return {}

    total_pending = len(pending)
    print(f"[check] {total_pending} {mode} domains to process.")

    # Initialize BrowserPool for immediate screenshotting of confirmed gambling sites
    browser_pool = BrowserPool(concurrency=min(concurrency, 10))
    # ponytail: Guard setup exceptions (e.g. missing Chromium binary) to avoid bare process crash
    try:
        await browser_pool.start()
    except Exception as e:
        print(f"[check FATAL] Could not start browser pool: {type(e).__name__}: {e}")
        print("[check FATAL] If Chromium is missing, run: playwright install chromium")
        return {"error": str(e), "gambling": 0, "regular": 0, "for_sale": 0, "unconfirmed": 0, "blocked": 0, "dead": 0}

    fetch_sem = asyncio.Semaphore(concurrency)
    pbar = tqdm(total=total_pending, desc=desc_label, unit="domain", dynamic_ncols=True)
    pbar.set_postfix({"Left": total_pending})

    run_stats = {
        "gambling": 0,
        "ai_evaluated": 0,
        "ai_gambling": 0,
        "regular": 0,
        "for_sale": 0,
        "unconfirmed": 0,
        "blocked": 0,
        "dead": 0,
        "screenshots_taken": 0,
        "gambling_screenshot_pending": 0,  # confirmed gambling, evidentiary screenshot still owed
    }

    async def process(doc):
        try:
            raw_domain = doc.get("domain") or doc.get("_id") or ""
            if not raw_domain:
                return

            if str(raw_domain).startswith(("http://", "https://")):
                parsed = urllib.parse.urlparse(str(raw_domain))
                domain = parsed.netloc or str(raw_domain).split("/")[0]
                url = str(raw_domain).rstrip("/")
            else:
                domain = str(raw_domain).strip().rstrip("/")
                url = f"https://{domain}"

            # 1. Fetch HTML via fast Scrapling TLS impersonator
            async with fetch_sem:
                result = await fetch(url, domain, timeout_seconds=timeout, per_domain_delay=delay)

            eval_url = result.final_url or url
            fallback_ss_path = None

            # Non-English pages read as gibberish to the English keyword list and the
            # English-only AI prompt -- translate once here and use `classify_html` for
            # every classification step below. Cheap langdetect gate inside means this is
            # a no-op (returns result.html unchanged) for the overwhelming English-majority case.
            classify_html, _ = await translate_to_english_if_needed(result.html or "", url=eval_url)

            # Immediate check for confirmed parked/for-sale landers before running heavy Playwright
            # fallback. A parked-for-sale domain is reachable and rendering real content — it's
            # just not gambling and not dead, but it's also not a "regular" website with its own
            # content, so it gets its own status instead of being lumped into 'regular'.
            is_parked, parked_hits = is_parked_or_for_sale(result.html or "", html=result.html or "", url=eval_url)
            if result.failure_type == "parked" or is_parked:
                delete_screenshot(domain, output_screenshot_dir)
                await async_write_result(
                    domain,
                    url=eval_url,
                    status="for_sale",
                    reason=f"For Sale: Parked/For-Sale domain lander detected ({', '.join(parked_hits[:2]) if parked_hits else 'parked'})",
                    screenshot_taken=False,
                )
                run_stats["for_sale"] += 1
                return

            # True dead: confirmed 404 with no parked/for-sale lander content.
            if result.failure_type == "dead_confirmed":
                delete_screenshot(domain, output_screenshot_dir)
                await async_write_result(
                    domain,
                    url=eval_url,
                    status="dead",
                    reason="Dead: 404 Not Found",
                    screenshot_taken=False,
                )
                run_stats["dead"] += 1
                return

            # Playwright JS Rendering & OCR Fallback for blocked, thin-DOM, or image-poster sites.
            # Raw HTML length alone misses JS-SPA gambling sites: a React/Vue shell can ship
            # several KB of script/CSS boilerplate (clearing the 800-char raw threshold) while
            # having almost no visible text, so a non-anchored SPA gambling site would otherwise
            # be classified off near-empty extracted text and never get browser-rendered.
            visible_text_len = len(_extract_text(result.html or ""))
            needs_browser_fallback = (
                result.failure_type == "blocked"
                or (not result.html or len(result.html.strip()) < 800)
                or visible_text_len < 250
                or is_gambling_domain(eval_url)[0]
            )

            decision, matched_keywords = classify(classify_html, url=eval_url, keywords=keywords)

            if needs_browser_fallback and decision != "gambling":
                # Try Playwright full browser render to bypass JS cloaking / Cloudflare / Banner poster
                ss_path, ss_status, ss_reason = await browser_pool.capture_url(
                    eval_url, output_screenshot_dir, retries=1, keywords=keywords, domain=domain
                )
                if ss_path and is_valid_screenshot(ss_path):
                    fallback_ss_path = ss_path
                    # Re-classify with screenshot OCR text included
                    decision, matched_keywords = classify(
                        classify_html, url=eval_url, keywords=keywords, screenshot_input=ss_path
                    )

            # Vision last resort: a canvas/WebGL-rendered casino/slot game ships zero DOM
            # text AND zero OCR-readable banner text (no static text to read, just animated
            # game graphics), so a page can pass through the entire text pipeline above and
            # still land on "regular" purely because there was nothing for it to read. Only
            # spend the extra vision-model call on pages that actually look empty everywhere
            # else and where we already paid for a screenshot.
            if fallback_ss_path and decision != "gambling" and visible_text_len < 250:
                vision_res = await classify_screenshot_vision(fallback_ss_path, eval_url)
                if (
                    vision_res
                    and str(vision_res.get("verdict", "")).strip().lower() == "gambling"
                    and float(vision_res.get("confidence", 0)) >= 0.6
                ):
                    decision = "needs_ai"
                    matched_keywords = list(matched_keywords) + [
                        f"vision:{str(vision_res.get('visual_evidence', 'gambling_ui_detected'))[:60]}"
                    ]

            if result.failure_type and decision != "gambling" and not fallback_ss_path:
                if result.failure_type == "blocked":
                    final_status = "blocked"
                    final_reason = "Blocked: Cloudflare WAF / HTTP 403 Forbidden"
                elif result.failure_type == "server_error":
                    # Transient 5xx after retries — do NOT mark 'dead' (frozen, never
                    # auto-rechecked). Route to 'unconfirmed' so it's picked up again
                    # on the next run instead of being silently written off.
                    final_status = "unconfirmed"
                    final_reason = f"Unconfirmed: Server error (5xx) after retries, requeued for later re-check"
                else:
                    final_status = "dead"
                    final_reason = f"Dead: {result.error or 'Host unreachable / connection failed'}"

                delete_screenshot(domain, output_screenshot_dir)
                await async_write_result(domain, url=eval_url, status=final_status, reason=final_reason, screenshot_taken=False)
                run_stats[final_status] += 1
                return

            num_matched = len(matched_keywords)
            # Audit trail: which stage produced the verdict, and the AI's own confidence/
            # category when one was involved — persisted so a review queue can be built
            # afterwards without re-running anything (see write_result docstring).
            final_confidence = None
            final_category = None
            final_decided_by = "heuristic_score"
            final_ip = None
            final_asn = None
            final_ai_context = None

            if decision == "gambling":
                # Keyword / Anchor Triggered Gambling
                final_status = "gambling"
                final_reason = f"{num_matched} keywords matched"
                final_decided_by = "domain_anchor_strong" if any(str(k).startswith("domain_keyword(") or str(k).startswith("gambling_tld(") for k in matched_keywords) else "heuristic_score"
                run_stats["gambling"] += 1

            elif decision == "needs_ai":
                # Route to AI Classifier
                run_stats["ai_evaluated"] += 1

                # Hosting-cluster corroboration: if this domain's IP sits on an ASN we've
                # already found heavily concentrated with confirmed gambling domains, surface
                # that as extra context for the AI rather than deciding anything ourselves —
                # see get_gambling_asn_concentration() docstring for why this is a hint, not
                # a trigger. Only spent here (the already-small needs_ai bucket), not on
                # every domain.
                if asn_concentration:
                    try:
                        domain_ip = await asyncio.to_thread(resolve_ip, domain)
                        single_ip = (domain_ip[0] if isinstance(domain_ip, list) else domain_ip) if domain_ip else None
                        domain_asn = await asyncio.to_thread(resolve_asn, single_ip) if single_ip else None
                        if domain_asn and domain_asn in asn_concentration:
                            final_ip, final_asn = single_ip, domain_asn
                            matched_keywords = list(matched_keywords) + [
                                f"hosting_cluster:{domain_asn}(gambling_ratio={asn_concentration[domain_asn]})"
                            ]
                        elif single_ip:
                            final_ip, final_asn = single_ip, domain_asn
                    except Exception:
                        pass

                # Save the page context actually shown to the AI (title/meta/CTAs/body text)
                # so a human reviewer can later see exactly what the model reasoned over,
                # without re-fetching a site that may have changed or gone offline by then.
                try:
                    ai_title, ai_meta, ai_ctas, ai_body = clean_page_text(classify_html, max_chars=2500)
                    final_ai_context = {
                        "title": ai_title,
                        "meta_description": ai_meta,
                        "cta_buttons": ai_ctas,
                        "body_excerpt": ai_body[:1200],
                        "screenshot_used": bool(fallback_ss_path),
                    }
                except Exception:
                    final_ai_context = None

                # A domain that already has a real screenshot on disk was proven gambling at
                # some point -- fast_mode's whole point is skipping the Round 2 challenge for
                # speed, but that's exactly what let a single un-challenged Round 1 AI pass
                # flip 248 previously-confirmed-gambling domains to "regular" on recheck.
                # Force the full challenge whenever there's existing screenshot evidence to
                # overturn, regardless of which recheck mode this is.
                _has_prior_screenshot = any(
                    is_valid_screenshot(os.path.join(output_screenshot_dir, cand))
                    for cand in all_filename_candidates(eval_url, domain)
                )
                # Circuit breaker: if most of the last 10 AI calls already timed out,
                # Ollama is clearly struggling right now -- skip waiting out another one.
                # This only ever changes WHEN we give up (fast vs. after a full timeout),
                # never WHAT gets decided: the outcome is still "unconfirmed" for a later
                # retry, exactly what a real timeout would have produced anyway. Deciding
                # gambling/regular from the heuristic alone here would skip the AI review
                # this whole pipeline exists to provide -- not worth the time saved.
                if _timeout_mgr.recent_timeout_rate() >= 0.6:
                    ai_res = {"verdict": "unconfirmed", "reason": "AI circuit breaker: Ollama timing out on recent calls, skipped to avoid another wait"}
                else:
                    ai_res = await classify_with_challenge(
                        classify_html,
                        url=eval_url,
                        matched_keywords=matched_keywords,
                        fast_mode=(mode in ("unconfirmed", "regular", "dead", "blocked", "for_sale") or os.getenv("AI_FAST_MODE", "false").lower() == "true") and not _has_prior_screenshot and not full_recheck,
                    )
                ai_verdict = str(ai_res.get("verdict", "unconfirmed")).strip().lower()
                final_confidence = ai_res.get("confidence")
                final_category = ai_res.get("category")
                final_decided_by = (
                    "tiebreaker_resolved" if ai_res.get("category") == "tiebreaker_resolved" else
                    "validator_override" if ai_res.get("category") == "validator_override" else
                    "validator_confirmed" if ai_res.get("challenge_override") is False and "Validated:" in str(ai_res.get("reason", "")) else
                    "ai_round2_challenge" if ai_res.get("challenge_override") else "ai_round1"
                )

                if ai_verdict == "gambling":
                    final_status = "gambling"
                    run_stats["gambling"] += 1
                    run_stats["ai_gambling"] += 1
                    final_reason = ai_res.get("reason", "AI confirmed gambling")
                elif ai_verdict == "regular":
                    final_status = "regular"
                    run_stats["regular"] += 1
                    final_reason = ai_res.get("reason", "AI rejected: Regular website")
                    delete_screenshot(domain, output_screenshot_dir)

                    # Cloaking corroboration for the single highest-risk false-negative
                    # shape: a domain with its own gambling-keyword anchor (e.g. "bet"/
                    # "casino" in the name) that the AI still rejected as regular --
                    # review_queue.py already calls exactly this combination its
                    # highest-priority "Tier 1" case worth a human's eyes. This adds an
                    # automatic corroborating check via a second, non-browser-like fetch
                    # (borrowed from PySecAuditWebScanner's dual-UA cloaking technique --
                    # see checking_url/fetcher.py's check_cloaking()) rather than trusting
                    # the single original fetch. It deliberately never overrides the AI's
                    # verdict on its own -- a content-length/keyword diff across two fetches
                    # is corroborating evidence, not proof -- it only annotates the record
                    # so it surfaces distinctly for human review instead of disappearing
                    # into "regular" indistinguishable from an ordinary correct rejection.
                    # Bounded cost: only runs for this already-narrow gambling-anchor
                    # subset, never on every domain. Added 2026-08-24.
                    if is_gambling_domain(eval_url)[0]:
                        try:
                            cloak = await check_cloaking(eval_url, timeout_seconds=timeout, gambling_keywords=keywords)
                        except Exception:
                            cloak = None
                        if cloak and cloak.get("cloaking_suspected"):
                            final_reason = f"{final_reason} [CLOAKING SUSPECTED: {cloak.get('note')} -- flagged for human review]"
                            final_category = "cloaking_suspected"
                elif ai_verdict in ("dead", "blocked"):
                    final_status = ai_verdict
                    run_stats[ai_verdict] += 1
                    final_reason = ai_res.get("reason", f"AI identified site as {ai_verdict}")
                    delete_screenshot(domain, output_screenshot_dir)
                else:
                    # Ollama offline or timeout -> Unconfirmed fallback without deleting screenshot
                    final_status = "unconfirmed"
                    run_stats["unconfirmed"] += 1
                    final_reason = ai_res.get("reason") or "Unconfirmed: AI model was busy/offline, queued to re-run"

            else:
                # Less than threshold keywords: Strictly Regular Website
                final_status = "regular"
                if any(str(k).startswith("allowlist:") for k in matched_keywords):
                    final_decided_by = "trusted_allowlist"
                    final_reason = "Institutional allowlist match (trusted_domains.json)"
                else:
                    final_reason = f"{num_matched} keywords matched (regular)" if num_matched > 0 else "0 keywords matched"
                run_stats["regular"] += 1
                delete_screenshot(domain, output_screenshot_dir)

            # 3. Screenshot Capture & Error Management Rules
            # Strict Proof Requirement: Only capture if validated as gambling
            if final_status == "gambling":
                ss_path, ss_status, ss_reason = await browser_pool.capture_url(
                    eval_url, output_screenshot_dir, retries=2, keywords=keywords, domain=domain
                )
                if ss_path and is_valid_screenshot(ss_path):
                    run_stats["screenshots_taken"] += 1
                    await async_write_result(
                        domain,
                        url=eval_url,
                        status="gambling",
                        reason=final_reason,
                        screenshot_taken=True,
                        screenshot_failed_reason=None,
                        ip=final_ip,
                        asn=final_asn,
                        confidence=final_confidence,
                        category=final_category,
                        decided_by=final_decided_by,
                        ai_context=final_ai_context,
                        matched_keywords=matched_keywords,
                    )
                else:
                    # Screenshot capture failing does NOT mean the site isn't gambling --
                    # it means a separate, later, dedicated fetch (Playwright, not the
                    # original classifier fetch) hit a WAF block/dead-end/timeout. The
                    # content-based verdict (heuristic score and/or AI Analyst+Validator,
                    # already backed by real evidence in `final_reason`/`final_confidence`/
                    # `final_category`) used to get silently overwritten to
                    # blocked/dead/unconfirmed here, discarding a confirmed true positive
                    # purely because of unrelated screenshot infrastructure flakiness.
                    # Fixed 2026-08-24: keep status="gambling" and leave screenshot_taken
                    # False with a reason -- db/mongo_client.find_pending_capture() already
                    # exists specifically to re-attempt these later, so this now actually
                    # gets retried instead of being lost as a different status entirely.
                    delete_screenshot(domain, output_screenshot_dir)
                    await async_write_result(
                        domain,
                        url=eval_url,
                        status="gambling",
                        reason=final_reason,
                        screenshot_taken=False,
                        screenshot_failed_reason=f"{ss_status}: {ss_reason}",
                        ip=final_ip,
                        asn=final_asn,
                        confidence=final_confidence,
                        category=final_category,
                        decided_by=final_decided_by,
                        ai_context=final_ai_context,
                        matched_keywords=matched_keywords,
                    )
                    run_stats["gambling_screenshot_pending"] += 1
            else:
                if final_status != "unconfirmed":
                    delete_screenshot(domain, output_screenshot_dir)
                await async_write_result(
                    domain, url=eval_url, status=final_status, reason=final_reason, screenshot_taken=False,
                    ip=final_ip, asn=final_asn, confidence=final_confidence, category=final_category,
                    decided_by=final_decided_by, ai_context=final_ai_context, matched_keywords=matched_keywords,
                )

        except Exception as e:
            domain = doc.get("domain", "")
            if domain:
                try:
                    await async_write_result(
                        domain,
                        url=f"https://{domain}",
                        status="unconfirmed",
                        reason=f"Pipeline error: {type(e).__name__}: {e}",
                        screenshot_taken=False,
                    )
                    run_stats["unconfirmed"] += 1
                except Exception:
                    pass
        finally:
            pbar.update(1)
            pbar.set_postfix({"Left": max(0, total_pending - pbar.n)})

    queue = asyncio.Queue()
    for doc in pending:
        queue.put_nowait(doc)

    async def worker():
        while not queue.empty():
            try:
                doc = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await process(doc)
            finally:
                queue.task_done()

    try:
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await queue.join()
        for w in workers:
            w.cancel()
    finally:
        await browser_pool.close()
        await close_ai_session()
        pbar.close()

    print("\n" + "=" * 65)
    summary_title = "BLOCKED RE-CHECK SUMMARY" if mode == "blocked" else "TRIPLE-LOCK AI CAPTURE SUMMARY"
    print(f"                 {summary_title}                 ")
    print("=" * 65)
    print(f" Total Domains Processed           : {total_pending:,}")
    print(f"  * Confirmed Gambling Sites       : {run_stats['gambling']:,}")
    print(f"    - Captured Screenshots         : {run_stats['screenshots_taken']:,}")
    print(f"    - Evaluated by Ollama AI       : {run_stats['ai_evaluated']:,}")
    print(f"    - AI Confirmed Gambling        : {run_stats['ai_gambling']:,}")
    print(f"    - Challenge Round Overrides    : {run_stats.get('challenge_overrides', 0):,}")
    print(f"  * Regular Sites (Verified)       : {run_stats['regular']:,}")
    print(f"  * Domains For Sale (Parked)      : {run_stats['for_sale']:,}")
    if run_stats["unconfirmed"]:
        print(f"  * Unconfirmed (Queued to re-run) : {run_stats['unconfirmed']:,}")
    blocked_label = "Still Blocked (403 / WAF)" if mode == "blocked" else "Blocked (403 / WAF)"
    print(f"  * {blocked_label:<31}: {run_stats['blocked']:,}")
    print(f"  * Dead / Unreachable             : {run_stats['dead']:,}")
    print("=" * 65 + "\n")

    return run_stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="checking_url runner")
    parser.add_argument("--mode", default="new", choices=["new", "blocked", "unconfirmed", "regular", "dead", "for_sale", "gambling"], help="Target queue")
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("CHECK_CONCURRENCY", os.getenv("MAX_CONCURRENT_FETCHES", 20))))
    parser.add_argument("--limit", type=int, default=int(os.getenv("CHECK_LIMIT", 0)))
    parser.add_argument("--min-age-days", type=int, default=int(os.getenv("RECHECK_MIN_AGE_DAYS", 0)), help="Cooldown age threshold in days for regular/dead rechecks")
    parser.add_argument("--file", "--seed-file", help="Path to Excel/CSV/txt file to seed into domain_Listed before running")
    parser.add_argument("--full-recheck", action="store_true", help="Force the full Round 2 AI challenge even in modes that default to fast_mode")
    args = parser.parse_args()

    if args.file:
        from db.mongo_client import seed_file_to_domain_listed
        seed_file_to_domain_listed(args.file)

    from checking_url.ai_classifier import start_ollama_if_needed
    try:
        asyncio.run(start_ollama_if_needed())
    except Exception as e:
        print(f"[!] Local AI status check error: {e}")

    asyncio.run(run(concurrency=args.concurrency, limit=args.limit, mode=args.mode, min_age_days=args.min_age_days, full_recheck=args.full_recheck))
