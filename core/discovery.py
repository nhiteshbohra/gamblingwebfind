import asyncio
import aiohttp

async def search_keyword(keyword: str, keyword_id: int, db, searxng_base_url="http://127.0.0.1:8080", max_pages=4, delay=1.0):
    """Search SearXNG for keyword and return de-duplicated URL list.
    Marks last_used_at and increments times_used in DB per spec contract.
    """
    urls = []
    async with aiohttp.ClientSession() as session:
        for page in range(1, max_pages + 1):
            try:
                params = {'q': keyword, 'format': 'json', 'pageno': page}
                async with session.get(f"{searxng_base_url.rstrip('/')}/search", params=params, timeout=10) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    results = data.get('results', [])
                    if not results:
                        break
                    for r in results:
                        if 'url' in r:
                            urls.append(r['url'])
                await asyncio.sleep(delay)
            except Exception:
                break

    # Spec: discovery.py owns marking last_used_at / times_used after each search
    await db.touch_keyword(keyword_id)

    return list(dict.fromkeys(urls))
