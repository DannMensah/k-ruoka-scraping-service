import time
import json
import logging
import os

logger = logging.getLogger(__name__)

BASE_URL = "https://www.k-ruoka.fi/kr-api"
SITE_URL = "https://www.k-ruoka.fi"
API_HEADERS = {
    "x-k-build-number": "29159",
    "x-k-experiments": "ab4d.10001.0!d2ae.10003.0!a.00145.0!a.00150.0!a.00154.1",
}

# API constraints discovered via benchmarking
MAX_OFFER_CATEGORY_LIMIT = 25  # API returns 400 for anything above 25
SEARCH_OFFERS_PAGE_SIZE = 48   # search-offers returns up to 48 per page
DELAY_BETWEEN_CALLS = 0.5  # seconds between API calls to avoid rate-limiting
MAX_RETRIES = 2
RETRY_BACKOFF = 1.5  # seconds, multiplied by attempt number

# ---------------------------------------------------------------------------
# Browser transport — uses DrissionPage (real Chrome) to bypass Cloudflare
# ---------------------------------------------------------------------------

_browser = None  # ChromiumPage singleton


def _get_browser():
    """Lazy-init the DrissionPage browser singleton.

    Opens Chrome, navigates to K-Ruoka to solve any Cloudflare challenge,
    then keeps the tab open for subsequent fetch() calls.
    """
    global _browser
    if _browser is not None:
        return _browser

    from DrissionPage import ChromiumPage, ChromiumOptions

    opts = ChromiumOptions()

    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in chrome_paths:
        if os.path.isfile(p):
            opts.set_browser_path(p)
            break

    # Use a dedicated profile so we don't conflict with the user's Chrome
    profile_dir = os.path.join(os.path.dirname(__file__), ".chrome-profile")
    opts.set_user_data_path(profile_dir)

    logger.info("Launching Chrome via DrissionPage...")
    _browser = ChromiumPage(opts)

    # Navigate to K-Ruoka to trigger and (hopefully) resolve CF challenge
    _browser.get(SITE_URL + "/kauppa")
    _wait_for_cloudflare(_browser)

    return _browser


def _wait_for_cloudflare(page, timeout: int = 60):
    """Wait for Cloudflare challenge to clear. Prompts user if needed."""
    for i in range(timeout):
        title = page.title or ""
        if "moment" not in title.lower() and "verif" not in title.lower():
            logger.info("Cloudflare challenge cleared (title: %s)", title)
            return
        if i == 5:
            logger.warning(
                "Cloudflare challenge detected — if a 'Verify you are human' "
                "checkbox appeared in the Chrome window, please click it."
            )
        time.sleep(1)
    raise RuntimeError(
        "Cloudflare challenge did not resolve within %ds. "
        "Please solve it manually in the Chrome window and retry." % timeout
    )


def close_browser():
    """Shut down the browser when done."""
    global _browser
    if _browser is not None:
        try:
            _browser.quit()
        except Exception:
            pass
        _browser = None


class _FetchResponse:
    """Minimal response wrapper matching the interface used by validate_api_headers."""
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.text = body

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:200]}")


def _js_fetch(method: str, url: str, body: dict | None = None) -> _FetchResponse:
    """Execute a fetch() call from within the browser page context.

    This inherits all Chrome cookies (including cf_clearance) and uses
    Chrome's authentic TLS fingerprint, bypassing Cloudflare.
    """
    page = _get_browser()

    headers_js = json.dumps({
        "Accept": "application/json",
        "Content-Type": "application/json",
        **API_HEADERS,
    })

    if method.upper() == "GET":
        js = f"""
        return fetch("{url}", {{
            method: "GET",
            headers: {headers_js},
            credentials: "include"
        }}).then(async r => ({{
            status: r.status,
            body: await r.text()
        }}));
        """
    else:
        body_js = json.dumps(body or {})
        js = f"""
        return fetch("{url}", {{
            method: "POST",
            headers: {headers_js},
            credentials: "include",
            body: JSON.stringify({body_js})
        }}).then(async r => ({{
            status: r.status,
            body: await r.text()
        }}));
        """

    result = page.run_js(js)
    if result is None:
        raise RuntimeError(f"fetch() returned None for {method} {url}")

    return _FetchResponse(result["status"], result["body"])


def _build_query_string(params: dict) -> str:
    """Build a URL query string from a dict."""
    from urllib.parse import urlencode
    return urlencode({k: v for k, v in params.items() if v is not None})


def _post_raw(endpoint: str, payload: dict) -> _FetchResponse:
    """POST and return a response-like object (for health checks)."""
    url = f"{BASE_URL}/{endpoint}"
    return _js_fetch("POST", url, payload)


def _post(endpoint: str, payload: dict) -> dict:
    resp = _post_raw(endpoint, payload)
    resp.raise_for_status()
    return resp.json()


def _post_with_retry(endpoint: str, payload: dict) -> dict:
    """POST with retry and backoff for bulk operations."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _post(endpoint, payload)
        except Exception:
            if attempt == MAX_RETRIES:
                raise
            wait = RETRY_BACKOFF * (attempt + 1)
            logger.debug("Retry %d for %s, waiting %.1fs", attempt + 1, endpoint, wait)
            time.sleep(wait)


def _post_with_params(endpoint: str, params: dict) -> dict:
    qs = _build_query_string(params)
    url = f"{BASE_URL}/{endpoint}?{qs}" if qs else f"{BASE_URL}/{endpoint}"
    resp = _js_fetch("POST", url)
    resp.raise_for_status()
    return resp.json()


def _get(endpoint: str, params: dict) -> dict:
    qs = _build_query_string(params)
    url = f"{BASE_URL}/{endpoint}?{qs}" if qs else f"{BASE_URL}/{endpoint}"
    resp = _js_fetch("GET", url)
    resp.raise_for_status()
    return resp.json()


def fetch_offer_categories(store_id: str) -> dict:
    return _post("offer-categories", {"storeId": store_id})


def fetch_offer_category(
    store_id: str,
    category: dict,
    offset: int = 0,
    limit: int = 25,
    pricing: dict | None = None,
) -> dict:
    return _post("offer-category", {
        "storeId": store_id,
        "category": category,
        "offset": offset,
        "limit": limit,
        "pricing": pricing or {},
    })


def fetch_offers(
    store_id: str,
    offer_ids: list,
    pricing: dict | None = None,
) -> dict:
    return _post("fetch-offers", {
        "storeId": store_id,
        "offerIds": offer_ids,
        "pricing": pricing or {},
    })


def fetch_related_products(
    product_id: str,
    store_id: str,
    segment_id: int = 1565,
) -> dict:
    return _get(f"v2/products/{product_id}/related", {
        "storeId": store_id,
        "segmentId": segment_id,
    })


def search_stores(
    query: str = "",
    offset: int = 0,
    limit: int = 2000,
) -> dict:
    return _post("stores/search", {
        "query": query,
        "offset": offset,
        "limit": limit,
    })


def search_product(
    query: str,
    store_id: str,
    language: str = "fi",
    offset: int = 0,
    limit: int = 100,
    discount_filter: bool = False,
    is_tos_tr_offer: bool = False,
) -> dict:
    params = {
        "offset": offset,
        "language": language,
        "storeId": store_id,
        "limit": limit,
        "discountFilter": discount_filter,
        "isTosTrOffer": is_tos_tr_offer,
    }
    return _post_with_params(f"v2/product-search/{query}", params)


def search_offers(
    store_id: str,
    category_path: str,
    offset: int = 0,
    language: str = "fi",
) -> dict:
    """Search offers by category path (GET endpoint).

    Args:
        store_id: Store identifier (e.g., "N110")
        category_path: Category path slug (e.g., "liha-ja-kasviproteiinit")
        offset: Pagination offset (default: 0)
        language: Language code (default: "fi")

    Returns:
        Dict with keys:
            totalHits: int — total number of matching offers
            storeId: str
            results: list[dict] — offer objects (up to 50 per page)
            categoryName: str
            suggestions: list
    """
    return _get("search-offers/", {
        "storeId": store_id,
        "offset": offset,
        "categoryPath": category_path,
        "language": language,
    })


# ---------------------------------------------------------------------------
# Bulk / aggregation helpers
# ---------------------------------------------------------------------------

def fetch_all_stores() -> list[dict]:
    """Return all K-Ruoka stores in a single call."""
    resp = search_stores(query="", limit=2000)
    if isinstance(resp, list):
        return resp
    return resp.get("results", resp.get("stores", []))


def fetch_all_categories(store_id: str) -> list[dict]:
    """Return offer categories for a store."""
    resp = fetch_offer_categories(store_id=store_id)
    return resp.get("offerCategories", [])


def fetch_all_offers_for_category(
    store_id: str,
    slug: str,
    *,
    on_page: callable = None,
) -> dict:
    """Paginate through all offers in a single category.

    Returns dict with keys:
        category: slug
        totalHits: int
        offers: list[dict]   — all offer objects
        apiCalls: int
        elapsedSeconds: float
    """
    t0 = time.perf_counter()
    api_calls = 0
    all_offers: list[dict] = []
    offset = 0
    total_hits = None

    while True:
        time.sleep(DELAY_BETWEEN_CALLS)
        page = _post_with_retry("offer-category", {
            "storeId": store_id,
            "category": {"kind": "productCategory", "slug": slug},
            "offset": offset,
            "limit": MAX_OFFER_CATEGORY_LIMIT,
            "pricing": {},
        })
        api_calls += 1

        if total_hits is None:
            total_hits = page.get("totalHits", 0)

        offers = page.get("offers", [])
        all_offers.extend(offers)

        if on_page:
            on_page(slug, offset, len(offers), total_hits)

        # Stop when we have all offers or the page is empty
        if not offers or len(all_offers) >= total_hits:
            break

        offset += MAX_OFFER_CATEGORY_LIMIT

    elapsed = time.perf_counter() - t0
    return {
        "category": slug,
        "totalHits": total_hits or 0,
        "offers": all_offers,
        "apiCalls": api_calls,
        "elapsedSeconds": round(elapsed, 3),
    }


def fetch_all_offers_for_store(
    store_id: str,
    *,
    on_category_done: callable = None,
) -> dict:
    """Fetch ALL offers for a store across every category (old approach).

    Returns dict with keys:
        storeId: str
        categories: list of {category, totalHits, offers, apiCalls, elapsedSeconds}
        totalOffers: int
        totalApiCalls: int
        totalElapsedSeconds: float
    """
    t0 = time.perf_counter()
    total_api_calls = 1  # for the categories call

    categories_list = fetch_all_categories(store_id)
    time.sleep(DELAY_BETWEEN_CALLS)

    results = []
    total_offers = 0

    for cat in categories_list:
        slug = cat.get("slug", "")
        if not slug:
            continue

        cat_result = fetch_all_offers_for_category(store_id, slug)
        total_api_calls += cat_result["apiCalls"]
        total_offers += len(cat_result["offers"])
        results.append(cat_result)

        if on_category_done:
            on_category_done(slug, cat_result)

    elapsed = time.perf_counter() - t0
    return {
        "storeId": store_id,
        "categories": results,
        "totalOffers": total_offers,
        "totalApiCalls": total_api_calls,
        "totalElapsedSeconds": round(elapsed, 3),
    }


def search_all_offers_for_store(
    store_id: str,
    *,
    category_path: str = "",
    on_page: callable = None,
) -> dict:
    """Fetch ALL offers for a store using the search-offers endpoint.

    Uses the search_offers() (GET) endpoint which returns up to 48 results
    per page and supports empty categoryPath for all offers. This is more
    efficient than the category-by-category approach.

    Args:
        store_id: Store identifier (e.g., "N110")
        category_path: Optional category filter (empty = all offers)
        on_page: Callback(offset, page_count, total_hits) per page

    Returns dict with keys:
        storeId: str
        totalHits: int
        offers: list[dict]
        apiCalls: int
        elapsedSeconds: float
    """
    t0 = time.perf_counter()
    api_calls = 0
    all_offers: list[dict] = []
    offset = 0
    total_hits = None

    while True:
        time.sleep(DELAY_BETWEEN_CALLS)

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = search_offers(
                    store_id=store_id,
                    category_path=category_path,
                    offset=offset,
                )
                break
            except Exception:
                if attempt == MAX_RETRIES:
                    raise
                wait = RETRY_BACKOFF * (attempt + 1)
                logger.debug("Retry %d for search_offers, waiting %.1fs", attempt + 1, wait)
                time.sleep(wait)

        api_calls += 1

        if total_hits is None:
            total_hits = resp.get("totalHits", 0)

        results = resp.get("results", [])
        all_offers.extend(results)

        if on_page:
            on_page(offset, len(results), total_hits)

        if not results or len(all_offers) >= total_hits:
            break

        offset += len(results)  # search-offers uses offset, not page number

    elapsed = time.perf_counter() - t0
    return {
        "storeId": store_id,
        "totalHits": total_hits or 0,
        "offers": all_offers,
        "apiCalls": api_calls,
        "elapsedSeconds": round(elapsed, 3),
    }


def validate_api_headers() -> dict:
    """Quick health check that the K-Ruoka API is reachable via the browser.

    Returns dict with:
        ok: bool
        storesStatus: int
        searchOffersStatus: int
        errors: list[str]
    """
    errors = []
    statuses = {}

    # 1. stores/search
    try:
        resp = _post_raw("stores/search", {"query": "", "offset": 0, "limit": 1})
        statuses["storesStatus"] = resp.status_code
        if resp.status_code != 200:
            errors.append(f"stores/search returned {resp.status_code}")
    except Exception as e:
        statuses["storesStatus"] = 0
        errors.append(f"stores/search failed: {e}")

    time.sleep(DELAY_BETWEEN_CALLS)

    # 2. search-offers (primary endpoint)
    try:
        qs = _build_query_string({"storeId": "N110", "offset": 0, "categoryPath": "juomat", "language": "fi"})
        url = f"{BASE_URL}/search-offers/?{qs}"
        resp = _js_fetch("GET", url)
        statuses["searchOffersStatus"] = resp.status_code
        if resp.status_code != 200:
            errors.append(f"search-offers returned {resp.status_code}")
    except Exception as e:
        statuses["searchOffersStatus"] = 0
        errors.append(f"search-offers failed: {e}")

    return {
        "ok": len(errors) == 0,
        **statuses,
        "errors": errors,
    }