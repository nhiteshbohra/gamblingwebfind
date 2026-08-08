import urllib.parse
import tldextract

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'msockid', 'msclkid', 'ref', 'ref_id', 'aff_id',
    'session_id', 'clickid', 'affid', 'btag', 'tag', 'subid', 'cid'
}

def normalize_url(raw_url: str) -> str:
    parsed = urllib.parse.urlparse(raw_url.strip())
    scheme = parsed.scheme.lower()
    if not scheme:
        scheme = 'http'
    
    netloc = parsed.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    if ':' in netloc:
        host, port = netloc.split(':', 1)
        if (scheme == 'http' and port == '80') or (scheme == 'https' and port == '443'):
            netloc = host

    path = parsed.path
    if path.endswith('/') and len(path) > 1:
        path = path[:-1]

    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    filtered_params = [(k, v) for k, v in query_params if k.lower() not in TRACKING_PARAMS]
    filtered_params.sort()
    new_query = urllib.parse.urlencode(filtered_params)

    return urllib.parse.urlunparse((scheme, netloc, path, parsed.params, new_query, ''))

def extract_domain(url: str) -> str:
    extracted = tldextract.extract(url)
    if extracted.registered_domain:
        return extracted.registered_domain.lower()
    return extracted.domain.lower()

async def is_duplicate(url: str, db) -> bool:
    norm_url = normalize_url(url)
    return await db.is_url_known(norm_url)
