# K-Ruoka Scraping Service

A Python scraping service that syncs K-Ruoka (Finnish grocery retailer) offers to Supabase. Runs as a GitHub Actions workflow. Uses curl_cffi with Chrome TLS impersonation after Cloudflare bypass via FlareSolverr / 2Captcha / Patchright browser.

## Project Structure

- **sync_to_supabase.py** - Main sync script (GitHub Actions entry point). Fetches Helsinki-area stores, maps offers to food-vibe schema, upserts to Supabase.
- **helpers.py** - Transport layer: Cloudflare bypass strategies (FlareSolverr → 2Captcha → browser), curl_cffi HTTP sessions, K-Ruoka API wrappers, geo-filtering.
- **tests/** - pytest test suite (header validation, helper functions, bulk helpers)
- **requirements.txt** - Python dependencies
- **scripts/** - Utility scripts for local testing and debugging
- **examples/** - Example API response payloads
- **.github/workflows/sync-k-ruoka.yml** - GitHub Actions workflow (triggered by Vercel cron or manually)

## Cloudflare Bypass (Transport Layer)

K-Ruoka uses Cloudflare Turnstile protection. The service resolves CF challenges using a multi-strategy approach, then all subsequent API calls use curl_cffi with Chrome TLS impersonation and the obtained `cf_clearance` cookies.

**Bypass strategies (tried in order):**

1. **FlareSolverr** — free Docker service, runs as a GitHub Actions sidecar
2. **2Captcha** — paid Turnstile solving (cheap fallback)
3. **Direct browser** — Patchright with Turnstile auto-click (unreliable last resort)

**Key components in helpers.py:**

- `_ensure_session()` — lazy-init curl_cffi session with CF cookies
- `_resolve_cloudflare()` — tries each bypass strategy in order
- `_http_request()` — rate-limited HTTP request with 429 backoff
- `_FetchResponse` — minimal response wrapper with `.status_code`, `.text`, `.json()`
- `close_browser()` — cleanup
- `fetch_helsinki_stores()` — geo-filtered stores within 50km of Helsinki
- `search_all_offers_for_store()` — paginated offer fetching via search-offers API
- `fetch_offers()` — fetch detailed offer data by offer IDs (used for compound offers)

## GitHub Actions Workflow

The sync runs via `.github/workflows/sync-k-ruoka.yml`:

- **Triggers:** `repository_dispatch` (from Vercel cron), `workflow_dispatch` (manual)
- **Services:** FlareSolverr Docker sidecar on port 8191
- **Steps:** Checkout → Python 3.12 → `pip install` → Patchright browsers (fallback) → Xvfb → Wait for FlareSolverr → Restore Chrome profile cache → `python sync_to_supabase.py` → Save Chrome profile cache
- **Timeout:** 120 minutes
- **Secrets:** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `CAPTCHA_API_KEY` (optional)

## K-Ruoka API Functions (helpers.py)

| Function                                                  | Description                                   |
| --------------------------------------------------------- | --------------------------------------------- |
| `fetch_offer_categories(store_id)`                        | Offer categories for a store                  |
| `fetch_offer_category(store_id, category, offset, limit)` | Paginated offers for a category               |
| `fetch_offers(store_id, offer_ids)`                       | Detailed offer data by IDs                    |
| `fetch_related_products(product_id, store_id)`            | Related products                              |
| `search_stores(query, offset, limit)`                     | Store search                                  |
| `search_product(query, store_id, ...)`                    | Product keyword search                        |
| `search_offers(store_id, category_path, offset)`          | Offers by category path                       |
| `fetch_all_stores()`                                      | All K-Ruoka stores                            |
| `fetch_all_categories(store_id)`                          | All categories for a store                    |
| `fetch_all_offers_for_category(store_id, slug)`           | All offers for a category (paginated)         |
| `search_all_offers_for_store(store_id)`                   | All offers for a store (flat list, preferred) |
| `fetch_helsinki_stores()`                                 | Stores within 50km of Helsinki                |
| `validate_api_headers()`                                  | Health-check: verify API is reachable         |

## K-Ruoka API Constraints

- **1,060 stores** total (~100-150 within Helsinki 50km)
- **search-offers page size: 48** (returns up to 48 per request)
- **offer-category max page size: 25** (API returns 400 for limit > 25)
- **Rate limiting:** 0.5s delays between calls work reliably
- **fetch-offers batch:** accepts multiple offer IDs per call; batched at 25 IDs per request
- **search-offers saves 43% of API calls** compared to category-by-category

## Sync Logic (sync_to_supabase.py)

1. Fetch Helsinki-area stores → upsert to `stores` table
2. For each store, fetch all offers via `search_all_offers_for_store()`
3. Map offers via `map_offer()` or `map_compound_product()`:
   - **Skip** offers where `availability.store` is `false`
   - **Skip** offers where `price >= normal_price` (no real discount)
   - **Batch-expand** compound offers (no embedded product) via `fetch_offers()` with up to 25 IDs per call → individual product rows
   - Extract batch unit price from `mobilescan.pricing.batch.unitPrice`
   - Store `quantity_required` from `mobilescan.pricing.batch.amount`
   - Reverse `raw_categories` (leaf → top for UI display)
4. Upsert products (by EAN) → fetch product UUIDs → attach `canonical_product_id`
5. Upsert offers → delete stale offers
6. Fail job if >25% of stores error

## Setup

### Prerequisites

- Python 3.12+
- Virtual environment

### Installation

```bash
python -m venv venv
.\venv\Scripts\Activate.ps1  # Windows PowerShell
source venv/bin/activate      # Unix/macOS
pip install -r requirements.txt
```

### Running Locally

The GitHub Actions workflow uses FlareSolverr (Docker) for Cloudflare bypass. To replicate locally:

```powershell
# 1. Start FlareSolverr (requires Docker Desktop)
docker run -d --name flaresolverr -p 8191:8191 -e LOG_LEVEL=info -e TZ=Europe/Helsinki ghcr.io/flaresolverr/flaresolverr:latest

# 2. Set environment variables
$env:FLARESOLVERR_URL = "http://localhost:8191/v1"
$env:SUPABASE_URL = "..."
$env:SUPABASE_SERVICE_ROLE_KEY = "..."

# 3. Activate venv
.\venv\Scripts\Activate.ps1

# 4. Verify API is reachable through FlareSolverr
python -c "from helpers import validate_api_headers; print(validate_api_headers())"

# 5. Run full sync
python sync_to_supabase.py
```

> ⚠️ **Full sync takes ~1-2 hours** (100-150 stores, ~60-90s each). For development and testing, use the profiling script with 1-2 stores instead (see below).

### Profiling & Testing with Few Stores

Use `scripts/profile_batching.py` to test the pipeline without writing to Supabase. It profiles fetch, mapping, and compound-offer batching for specific stores.

```powershell
# Test with 1-2 stores (takes ~1-3 min per store)
python scripts/profile_batching.py N110 N111
```

**Typical performance per store (measured 2026-02-23):**

| Phase                                            | Time       | Notes                                 |
| ------------------------------------------------ | ---------- | ------------------------------------- |
| Fetch all offers (`search_all_offers_for_store`) | 40-65s     | 57-63 API calls, rate-limited at 0.5s |
| Map regular offers                               | 4-6ms      | ~10-14 µs/offer, negligible           |
| Batch-fetch compound offers                      | 20-22s     | 19-21 batched calls (25 IDs each)     |
| Map compound products                            | 9-11ms     | ~6.5 µs/product, negligible           |
| **Total per store**                              | **60-90s** | Network I/O is 99.9%+ of runtime      |

**Key findings:**

- Mapping/processing is negligible (<20ms total per store)
- All time is spent in network I/O (API calls with 0.5s rate limit)
- Compound offer batching (25 IDs/call) reduces compound fetch from ~500 calls to ~20 calls per store
- The 0.5s rate limit is at its minimum — do not reduce further

### Running Tests

```bash
python -m pytest tests/ -v
```

Note: Tests require network access and will trigger Cloudflare bypass on first run.

## Dependencies

- **patchright** — Playwright-based browser automation (Cloudflare bypass fallback)
- **supabase** — Supabase Python client for DB writes
- **requests** — HTTP client (for scripts and FlareSolverr calls)
- **curl_cffi** — HTTP client with Chrome TLS impersonation
- **pytest** — Test framework
