"""
K-Ruoka API helpers — transport layer with Cloudflare bypass.

CF bypass strategies (tried in order):
  1. FlareSolverr  – free Docker service, runs on GitHub Actions
  2. 2Captcha      – paid Turnstile solving ($3 deposit lasts ~1 year)
  3. Direct browser – Patchright headless with Turnstile auto-click (unreliable)

After the CF challenge is solved, all API calls use curl_cffi with Chrome TLS
impersonation + the obtained cf_clearance cookies.  This is much faster than
browser-based fetch().
"""
import time
import json
import logging
import math
import os
import threading
from urllib.parse import urlencode

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
MAX_RETRIES = 2
RETRY_BACKOFF = 1.5             # seconds, multiplied by attempt number

# Global rate limiting — enforces max request rate across ALL threads
# 0.5s = 2 req/s, proven safe from benchmarking (see AGENTS.md).
GLOBAL_MIN_INTERVAL = 0.5       # seconds between requests (~2 req/s)
MAX_429_RETRIES = 4              # retry attempts on HTTP 429
INITIAL_429_BACKOFF = 15.0       # first 429 backoff; doubles each retry
MAX_403_RETRIES = 1              # re-auth attempts on HTTP 403

# Helsinki geo-filtering
HELSINKI_LAT = 60.1699
HELSINKI_LON = 24.9384
MAX_DISTANCE_KM = 50


# ---------------------------------------------------------------------------
# CF bypass session management
# ---------------------------------------------------------------------------

_cf_cookies: dict[str, str] = {}  # shared CF cookies (read-only after init)
_cf_user_agent: str = ""
_init_lock = threading.Lock()  # one-time initialisation guard
_initialised = False
_thread_local = threading.local()  # per-thread curl_cffi sessions

# Global rate-limiter state
_rate_lock = threading.Lock()
_last_request_time = 0.0


def _ensure_session():
    """Get or create an authenticated curl_cffi session for the current thread.

    The first call resolves Cloudflare (inside a lock).  Subsequent calls
    (including from worker threads) create a lightweight per-thread session
    that shares the same cookies, enabling true parallel HTTP requests.
    """
    global _cf_cookies, _cf_user_agent, _initialised

    # One-time CF resolution
    if not _initialised:
        with _init_lock:
            if not _initialised:
                from curl_cffi.requests import Session as _Sess

                cookies, user_agent = _resolve_cloudflare()
                _cf_cookies = cookies
                _cf_user_agent = user_agent

                # Create the first session and verify it works
                s = _Sess(impersonate="chrome")
                s.headers.update({
                    "User-Agent": _cf_user_agent,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    **API_HEADERS,
                })
                for name, value in _cf_cookies.items():
                    s.cookies.set(name, value, domain=".k-ruoka.fi")

                _thread_local.session = s
                _verify_session(s)
                _initialised = True

    # Per-thread session (lazily created)
    s = getattr(_thread_local, "session", None)
    if s is not None:
        return s

    from curl_cffi.requests import Session as _Sess

    s = _Sess(impersonate="chrome")
    s.headers.update({
        "User-Agent": _cf_user_agent,
        "Accept": "application/json",
        "Content-Type": "application/json",
        **API_HEADERS,
    })
    for name, value in _cf_cookies.items():
        s.cookies.set(name, value, domain=".k-ruoka.fi")
    _thread_local.session = s
    return s


def _verify_session(session):
    """Quick smoke-test that the session can reach K-Ruoka API."""
    resp = session.post(
        f"{BASE_URL}/stores/search",
        json={"query": "", "offset": 0, "limit": 1},
    )
    if resp.status_code == 403:
        logger.warning("Session verification got 403 — CF cookies may be invalid")
        raise RuntimeError("CF session is not valid (403)")
    logger.info("Session verified (stores/search → %d)", resp.status_code)


def _resolve_cloudflare() -> tuple[dict, str]:
    """Resolve the CF challenge using the best available strategy.

    Returns (cookies_dict, user_agent).
    """
    strategies = []

    # Strategy 1: FlareSolverr (free, Docker service)
    if os.environ.get("FLARESOLVERR_URL"):
        strategies.append(("FlareSolverr", _resolve_cf_flaresolverr))

    # Strategy 2: 2Captcha (cheap, reliable)
    if os.environ.get("CAPTCHA_API_KEY"):
        strategies.append(("2Captcha", _resolve_cf_2captcha))

    # Strategy 3: Direct browser (auto-click, unreliable)
    strategies.append(("Browser", _resolve_cf_browser))

    last_error = None
    for name, fn in strategies:
        try:
            logger.info("Trying CF bypass: %s...", name)
            cookies, ua = fn()
            logger.info(
                "CF bypass succeeded with %s (cookies: %s)",
                name, list(cookies.keys()),
            )
            return cookies, ua
        except Exception as e:
            logger.warning("CF bypass via %s failed: %s", name, e)
            last_error = e

    raise RuntimeError(
        f"All CF bypass strategies failed. Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# Strategy 1: FlareSolverr
# ---------------------------------------------------------------------------

def _resolve_cf_flaresolverr() -> tuple[dict, str]:
    """Use FlareSolverr service to solve Cloudflare challenge."""
    import requests as stdlib_requests

    url = os.environ["FLARESOLVERR_URL"]
    logger.info("FlareSolverr request to %s...", url)

    resp = stdlib_requests.post(url, json={
        "cmd": "request.get",
        "url": f"{SITE_URL}/kauppa",
        "maxTimeout": 90000,
    }, timeout=120)

    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(
            f"FlareSolverr returned status={data.get('status')}: {data}"
        )

    solution = data["solution"]
    cookies = {}
    for c in solution.get("cookies", []):
        cookies[c["name"]] = c["value"]

    user_agent = solution.get("userAgent", "")
    if "cf_clearance" not in cookies:
        raise RuntimeError(
            "FlareSolverr did not return cf_clearance cookie"
        )

    logger.info(
        "FlareSolverr OK — got %d cookies, UA=%s",
        len(cookies), user_agent[:60],
    )
    return cookies, user_agent


# ---------------------------------------------------------------------------
# Strategy 2: 2Captcha (Turnstile solver)
# ---------------------------------------------------------------------------

def _resolve_cf_2captcha() -> tuple[dict, str]:
    """Use 2Captcha to solve the Cloudflare Turnstile, then extract cookies."""
    api_key = os.environ["CAPTCHA_API_KEY"]
    sitekey = _get_turnstile_sitekey()

    logger.info("Sending Turnstile to 2Captcha (sitekey=%s)...", sitekey[:20])

    import requests as stdlib_requests

    # Submit the task
    resp = stdlib_requests.post("https://2captcha.com/in.php", data={
        "key": api_key,
        "method": "turnstile",
        "sitekey": sitekey,
        "pageurl": f"{SITE_URL}/kauppa",
        "json": 1,
    }, timeout=30)
    result = resp.json()
    if result.get("status") != 1:
        raise RuntimeError(f"2Captcha submit failed: {result}")

    task_id = result["request"]
    logger.info("2Captcha task submitted: %s — polling...", task_id)

    # Poll for the solved token (up to 120s)
    token = None
    for _ in range(40):
        time.sleep(5)
        resp = stdlib_requests.get("https://2captcha.com/res.php", params={
            "key": api_key,
            "action": "get",
            "id": task_id,
            "json": 1,
        }, timeout=15)
        result = resp.json()
        if result.get("status") == 1:
            token = result["request"]
            break
        if result.get("request") != "CAPCHA_NOT_READY":
            raise RuntimeError(f"2Captcha error: {result}")

    if not token:
        raise RuntimeError("2Captcha did not return a solution in time")

    logger.info("2Captcha solved! Injecting token via browser...")

    # Use Patchright to inject the solved token and harvest cookies
    return _inject_turnstile_token(token)


def _get_turnstile_sitekey() -> str:
    """Fetch the K-Ruoka page HTML and extract the Turnstile sitekey."""
    import re
    from curl_cffi import requests as cf_requests

    resp = cf_requests.get(
        f"{SITE_URL}/kauppa", impersonate="chrome", timeout=15,
    )
    html = resp.text

    # Pattern 1: data-sitekey="..."
    match = re.search(r'data-sitekey="([^"]+)"', html)
    if match:
        return match.group(1)
    # Pattern 2: sitekey: '...' or siteKey: '...'
    match = re.search(r"site[Kk]ey[\"\\s:]+[\"']([^\"']+)[\"']", html)
    if match:
        return match.group(1)
    # Pattern 3: turnstile render call
    match = re.search(
        r"turnstile\.render\([^)]*sitekey[\"\\s:]+[\"']([^\"']+)", html,
    )
    if match:
        return match.group(1)

    raise RuntimeError("Could not find Turnstile sitekey in page HTML")


def _inject_turnstile_token(token: str) -> tuple[dict, str]:
    """Start a browser, load the CF challenge page, inject the solved token,
    and return the resulting cookies + user agent."""
    from patchright.sync_api import sync_playwright

    pw = sync_playwright().start()
    profile_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".chrome-profile",
    )
    os.makedirs(profile_dir, exist_ok=True)

    try:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            args=["--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(
            f"{SITE_URL}/kauppa",
            wait_until="domcontentloaded", timeout=60000,
        )
        time.sleep(3)

        # Inject the token via Turnstile callback
        safe_token = token.replace("\\", "\\\\").replace('"', '\\"')
        page.evaluate(
            """
            (() => {
                // Find the Turnstile response element and set it
                const el = document.querySelector(
                    'input[name="cf-turnstile-response"],'
                    + ' textarea[name="cf-turnstile-response"]'
                );
                if (el) el.value = "%s";

                // Try the cf_chl_opt callback
                if (typeof window._cf_chl_opt !== 'undefined'
                    && window._cf_chl_opt.chlApiCb) {
                    window._cf_chl_opt.chlApiCb("%s");
                }
            })()
            """
            % (safe_token, safe_token)
        )
        time.sleep(5)

        # Wait for CF to resolve
        for _ in range(30):
            try:
                title = page.title() or ""
            except Exception:
                time.sleep(2)
                continue
            if "moment" not in title.lower() and "verif" not in title.lower():
                break
            time.sleep(1)

        # Extract cookies
        cookies_list = ctx.cookies()
        cookies = {
            c["name"]: c["value"]
            for c in cookies_list
            if "k-ruoka" in c.get("domain", "")
        }
        ua = page.evaluate("navigator.userAgent") or ""

        if "cf_clearance" not in cookies:
            raise RuntimeError(
                "Token injection did not produce cf_clearance cookie"
            )

        return cookies, ua
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Strategy 3: Direct browser (auto-click — unreliable last resort)
# ---------------------------------------------------------------------------

def _resolve_cf_browser() -> tuple[dict, str]:
    """Launch Patchright browser and try to auto-click Turnstile."""
    from patchright.sync_api import sync_playwright

    pw = sync_playwright().start()
    profile_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".chrome-profile",
    )
    os.makedirs(profile_dir, exist_ok=True)

    try:
        logger.info("Launching Patchright browser for direct CF bypass...")
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",   # system Chrome for best fingerprint
            headless=False,
            args=["--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1920, "height": 1080},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        logger.info("Navigating to K-Ruoka...")
        page.goto(
            f"{SITE_URL}/kauppa",
            wait_until="domcontentloaded", timeout=60000,
        )

        # Wait for auto-resolve or click Turnstile
        _wait_for_cloudflare_browser(page)

        # Extract cookies
        cookies_list = ctx.cookies()
        cookies = {
            c["name"]: c["value"]
            for c in cookies_list
            if "k-ruoka" in c.get("domain", "")
        }
        ua = page.evaluate("navigator.userAgent") or ""

        if "cf_clearance" not in cookies:
            raise RuntimeError(
                "Browser could not obtain cf_clearance cookie"
            )

        return cookies, ua
    finally:
        try:
            ctx.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


def _wait_for_cloudflare_browser(page, timeout: int = 90):
    """Wait for CF to resolve in browser, trying Turnstile click."""
    clicked = False
    for i in range(timeout):
        try:
            title = page.title() or ""
        except Exception:
            logger.info("Page navigating... waiting for reload")
            time.sleep(3)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            continue

        if "moment" not in title.lower() and "verif" not in title.lower():
            logger.info("CF cleared (title: %s)", title)
            return

        if i == 5:
            logger.warning("CF challenge detected — attempting auto-click...")

        # Try Turnstile click once at t=5s, again at t=30s
        if not clicked and i in (5, 30):
            _try_click_turnstile_browser(page)
            clicked = i == 30

        time.sleep(1)

    raise RuntimeError(f"CF challenge did not resolve within {timeout}s")


def _try_click_turnstile_browser(page):
    """Attempt to click the Turnstile checkbox in browser."""
    try:
        for frame in page.frames:
            if "challenges.cloudflare.com" not in (frame.url or ""):
                continue
            try:
                body = frame.locator("body")
                if body.is_visible(timeout=2000):
                    body.click(position={"x": 28, "y": 28})
                    logger.info("Clicked Turnstile widget area")
                    return
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Session teardown
# ---------------------------------------------------------------------------

def close_browser():
    """Clean up session resources."""
    global _initialised
    s = getattr(_thread_local, "session", None)
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
        _thread_local.session = None
    _initialised = False


def _re_authenticate():
    """Invalidate current CF session and re-resolve Cloudflare.

    Called when a 403 response suggests the CF cookies have expired.
    Resets the global init flag so the next ``_ensure_session()`` call
    triggers a fresh CF bypass.
    """
    global _initialised, _cf_cookies, _cf_user_agent
    logger.warning("Re-authenticating — resetting CF session...")

    # Close current thread session
    s = getattr(_thread_local, "session", None)
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
        _thread_local.session = None

    # Reset global state so _ensure_session() re-resolves CF
    with _init_lock:
        _cf_cookies = {}
        _cf_user_agent = ""
        _initialised = False

    # Trigger fresh CF bypass + session creation
    _ensure_session()
    logger.info("Re-authentication complete — new CF session established")


# ---------------------------------------------------------------------------
# HTTP transport (uses curl_cffi session with CF cookies)
# ---------------------------------------------------------------------------

class _FetchResponse:
    """Minimal response wrapper for compatibility."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.text = body

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:200]}")


def _build_query_string(params: dict) -> str:
    """Build a URL query string from a dict."""
    return urlencode({k: v for k, v in params.items() if v is not None})


def _rate_limit_wait():
    """Reserve the next request slot and sleep until it arrives.

    Uses a lock-free reservation pattern: each caller reserves a future
    timestamp (at least GLOBAL_MIN_INTERVAL after the previous one),
    then sleeps outside the lock so other threads can also reserve.
    """
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        wait_time = max(0.0, GLOBAL_MIN_INTERVAL - (now - _last_request_time))
        _last_request_time = now + wait_time
    if wait_time > 0:
        time.sleep(wait_time)


def _http_request(
    method: str, url: str, body: dict | None = None,
) -> _FetchResponse:
    """Make an HTTP request with global rate limiting, 429 retry, and 403 re-auth."""
    global _last_request_time

    retries_403 = 0

    for attempt in range(MAX_429_RETRIES + 1):
        _rate_limit_wait()

        session = _ensure_session()
        if method.upper() == "GET":
            resp = session.get(url)
        else:
            resp = session.post(url, json=body)

        # 403 Forbidden — likely expired CF cookies, re-authenticate once
        if resp.status_code == 403 and retries_403 < MAX_403_RETRIES:
            retries_403 += 1
            logger.warning(
                "HTTP 403 — CF cookies may have expired, re-authenticating "
                "(attempt %d/%d)", retries_403, MAX_403_RETRIES,
            )
            try:
                _re_authenticate()
            except Exception as e:
                logger.error("Re-authentication failed: %s", e)
                return _FetchResponse(resp.status_code, resp.text)
            continue

        if resp.status_code != 429:
            return _FetchResponse(resp.status_code, resp.text)

        # 429 Too Many Requests — back off and pause all threads
        if attempt < MAX_429_RETRIES:
            backoff = INITIAL_429_BACKOFF * (2 ** attempt)
            logger.warning(
                "HTTP 429 — backing off %.1fs (attempt %d/%d)",
                backoff, attempt + 1, MAX_429_RETRIES,
            )
            # Push the global rate limiter forward to pause all threads
            with _rate_lock:
                future = time.monotonic() + backoff
                if future > _last_request_time:
                    _last_request_time = future
            time.sleep(backoff)
        else:
            logger.error("HTTP 429 after %d retries, giving up", MAX_429_RETRIES)

    return _FetchResponse(resp.status_code, resp.text)


def _post_raw(endpoint: str, payload: dict) -> _FetchResponse:
    """POST and return a response-like object (for health checks)."""
    url = f"{BASE_URL}/{endpoint}"
    return _http_request("POST", url, payload)


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
            logger.debug(
                "Retry %d for %s, waiting %.1fs",
                attempt + 1, endpoint, wait,
            )
            time.sleep(wait)


def _post_with_params(endpoint: str, params: dict) -> dict:
    qs = _build_query_string(params)
    url = f"{BASE_URL}/{endpoint}?{qs}" if qs else f"{BASE_URL}/{endpoint}"
    resp = _http_request("POST", url)
    resp.raise_for_status()
    return resp.json()


def _get(endpoint: str, params: dict) -> dict:
    qs = _build_query_string(params)
    url = f"{BASE_URL}/{endpoint}?{qs}" if qs else f"{BASE_URL}/{endpoint}"
    resp = _http_request("GET", url)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# K-Ruoka API endpoint wrappers
# ---------------------------------------------------------------------------

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
        result = _post_with_retry("offer-category", {
            "storeId": store_id,
            "category": {"kind": "productCategory", "slug": slug},
            "offset": offset,
            "limit": MAX_OFFER_CATEGORY_LIMIT,
            "pricing": {},
        })
        api_calls += 1

        if total_hits is None:
            total_hits = result.get("totalHits", 0)

        offers = result.get("offers", [])
        all_offers.extend(offers)

        if on_page:
            on_page(slug, offset, len(offers), total_hits)

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
    total_api_calls = 1

    categories_list = fetch_all_categories(store_id)

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
    """Fetch ALL offers for a store via category-based sequential fetching.

    Strategy:
      1. GET offer-categories → list of category slugs
      2. For each category, paginate offer-category (limit 25) to get all offers
      3. Fetch categories sequentially with global rate limiting (2 req/s)
      4. Deduplicate offers by offer ID (same offer can appear in multiple categories)

    This avoids the search-offers offset-1000 hard limit and is significantly
    faster than sequential fetching thanks to parallelism.

    Args:
        store_id: Store identifier (e.g., "N110")
        category_path: Ignored (kept for API compatibility)
        on_page: Callback(offset, page_count, total_hits) per page (called per category)

    Returns dict with keys:
        storeId: str
        totalHits: int
        offers: list[dict]
        apiCalls: int
        elapsedSeconds: float
    """
    t0 = time.perf_counter()
    api_calls = 0

    # 1. Fetch categories
    categories = fetch_all_categories(store_id)
    api_calls += 1
    slugs = [c.get("slug", "") for c in categories if c.get("slug")]

    if not slugs:
        logger.warning("Store %s: no offer categories found", store_id)
        return {
            "storeId": store_id,
            "totalHits": 0,
            "offers": [],
            "apiCalls": api_calls,
            "elapsedSeconds": round(time.perf_counter() - t0, 3),
        }

    # 2. Fetch all offers per category sequentially
    #    Sequential avoids thundering-herd after 429 backoff and keeps
    #    the request rate predictable at GLOBAL_MIN_INTERVAL.
    all_offers_by_id: dict[str, dict] = {}  # deduplicate by offer ID

    for slug in slugs:
        try:
            result = fetch_all_offers_for_category(store_id, slug)
            api_calls += result["apiCalls"]
            for offer in result["offers"]:
                oid = offer.get("id", "")
                if oid and oid not in all_offers_by_id:
                    all_offers_by_id[oid] = offer
        except Exception:
            logger.warning(
                "Store %s: category '%s' failed, skipping",
                store_id, slug, exc_info=True,
            )

    offers = list(all_offers_by_id.values())
    elapsed = time.perf_counter() - t0
    return {
        "storeId": store_id,
        "totalHits": len(offers),
        "offers": offers,
        "apiCalls": api_calls,
        "elapsedSeconds": round(elapsed, 3),
    }


# ---------------------------------------------------------------------------
# Helsinki geo-filtering
# ---------------------------------------------------------------------------

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two lat/lon points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def filter_stores_by_distance(
    stores: list[dict], lat: float, lon: float, max_km: float,
) -> list[dict]:
    """Filter stores to those within max_km of the given coordinates."""
    result = []
    for store in stores:
        geo = store.get("geo")
        if not geo:
            continue
        store_lat = geo.get("latitude")
        store_lon = geo.get("longitude")
        if store_lat is None or store_lon is None:
            continue
        dist = haversine(lat, lon, store_lat, store_lon)
        if dist <= max_km:
            result.append(store)
    return result


def fetch_helsinki_stores() -> list[dict]:
    """Fetch all K-Ruoka stores within 50km of Helsinki."""
    all_stores = fetch_all_stores()
    filtered = filter_stores_by_distance(
        all_stores, HELSINKI_LAT, HELSINKI_LON, MAX_DISTANCE_KM,
    )
    logger.info(
        "Helsinki filter: %d/%d stores within %dkm",
        len(filtered), len(all_stores), MAX_DISTANCE_KM,
    )
    return filtered


def validate_api_headers() -> dict:
    """Quick health check that the K-Ruoka API is reachable.

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
        resp = _post_raw(
            "stores/search", {"query": "", "offset": 0, "limit": 1},
        )
        statuses["storesStatus"] = resp.status_code
        if resp.status_code != 200:
            errors.append(f"stores/search returned {resp.status_code}")
    except Exception as e:
        statuses["storesStatus"] = 0
        errors.append(f"stores/search failed: {e}")

    # 2. search-offers (primary endpoint)
    try:
        qs = _build_query_string({
            "storeId": "N110", "offset": 0,
            "categoryPath": "juomat", "language": "fi",
        })
        url = f"{BASE_URL}/search-offers/?{qs}"
        resp = _http_request("GET", url)
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
