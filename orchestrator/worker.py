import re
from core.fetcher import fetch
from core.classifier import classify
from core.keyword_extractor import extract_keywords, extract_dorks
from core.dedup import extract_domain

async def _get_existing_keyword_terms(db) -> set:
    """Fetch current keyword terms from DB to pre-filter extractor output."""
    rows = await db.get_next_keywords(limit=500)  # ponytail: 500 cap avoids unbounded query; enough for pre-filter
    return {row['term'] for row in rows}

async def process_url(url_row, db, settings):
    url_id = url_row['id']
    url = url_row['url']
    domain = extract_domain(url)

    result = await fetch(
        url,
        domain=domain,
        db=db,
        timeout_seconds=settings.get('fetch_timeout_seconds', 10),
        per_domain_delay=settings.get('per_domain_delay_seconds', 2.0),
    )
    # touch_domain is handled inside fetcher.fetch()

    if result.error or not result.html or (result.status_code and result.status_code >= 400):
        await db.mark_status(url_id, 'dead', confidence_score=0.0,
                             reasons=[result.error or f"status:{result.status_code}"])
        return

    is_gambling, score, reasons = classify(result.html, domain=domain, url=url, threshold=settings.get('classification_threshold', 0.55))

    if is_gambling:
        await db.mark_status(url_id, 'verified', confidence_score=score, reasons=reasons)
        existing = await _get_existing_keyword_terms(db)
        new_keywords = extract_keywords(
            result.html,
            max_keywords=settings.get('max_new_keywords_per_page', 5),
            existing_terms=existing
        )
        
        # Domain brand extraction
        domain_parts = domain.replace('.com', '').replace('.in', '').replace('.org', '').replace('.net', '').replace('.co', '').split('.')
        base_name = domain_parts[-1] if domain_parts else domain
        clean_brand = re.sub(r'[^a-zA-Z0-9]+', ' ', base_name).strip().lower()
        brand_keywords = [clean_brand, f"{clean_brand} casino", f"{clean_brand} login", f"{clean_brand} app", f"{clean_brand} official"] if len(clean_brand) >= 3 else []

        all_new = list(dict.fromkeys(new_keywords + brand_keywords))
        filtered_new = [kw for kw in all_new if kw and len(kw) >= 3 and kw not in existing]

        if filtered_new:
            await db.insert_keywords([(kw, 'extracted', url, 'keyword') for kw in filtered_new])

        # Extract & insert candidate search-engine dorks
        extracted_dorks = extract_dorks(result.html, domain=domain)
        filtered_dorks = [dork for dork in extracted_dorks if dork not in existing]
        if filtered_dorks:
            await db.insert_keywords([(dork, 'extracted', url, 'dork') for dork in filtered_dorks])
    else:
        await db.mark_status(url_id, 'rejected', confidence_score=score, reasons=reasons)

