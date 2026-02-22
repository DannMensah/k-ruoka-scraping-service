# K-Ruoka Scraping Service

A Flask-based web scraping service that provides APIs to fetch product offers and store information from K-Ruoka (Finnish grocery retailer). Uses DrissionPage (real Chrome via DevTools Protocol) to bypass Cloudflare protection.

## Project Structure

- **scraper.py** - Flask application with API endpoints
- **helpers.py** - Helper functions for making API requests to K-Ruoka (DrissionPage transport layer)
- **tests/** - pytest test suite (header validation, helpers, endpoints)
- **requirements.txt** - Python dependencies
- **render.yaml** - Render deployment configuration
- **scripts/** - Simple scripts to call each endpoint locally
- **examples/** - Example response payloads

## Transport Layer (DrissionPage)

K-Ruoka uses Cloudflare Turnstile protection. The service uses DrissionPage to launch a real Chrome browser and execute `fetch()` calls from within the page context. This inherits Chrome's authentic TLS fingerprint and Cloudflare cookies, bypassing bot detection.

**How it works:**

1. On first API call, Chrome launches and navigates to k-ruoka.fi
2. If Cloudflare presents a "Verify you are human" challenge, a user must click the checkbox in the Chrome window (one-time per session)
3. All subsequent API calls use `fetch()` from within the browser page — inherits all cookies and TLS context
4. A dedicated Chrome profile is stored in `.chrome-profile/` (gitignored)

**Key components in helpers.py:**

- `_get_browser()` — lazy-init ChromiumPage singleton
- `_wait_for_cloudflare()` — polls page title, warns user to click Turnstile
- `_js_fetch(method, url, body)` — runs `fetch()` in browser JS context
- `_FetchResponse` — minimal response wrapper with `.status_code`, `.text`, `.json()`
- `close_browser()` — cleanup

## Endpoints

### POST `/offer-categories`

Fetches available offer categories for a store.

**Request (JSON):**

- `storeId`: Store identifier (e.g., "N110")

**Response:** JSON object with `offerCategories` array

### POST `/offer-category`

Fetches product offers for a specific category from K-Ruoka.

**Request (JSON):**

- `storeId`: Store identifier (e.g., "N110")
- `category`: Category object with `kind` and `slug` properties
- `offset`: Pagination offset (default: 0)
- `limit`: Results limit per page (default: 25)
- `pricing`: Pricing object (can be empty {})

**Response:** JSON object with `offers` array

### POST `/fetch-offers`

Fetches details for specific offers by offer IDs.

**Request (JSON):**

- `storeId`: Store identifier (e.g., "N110")
- `offerIds`: Array of offer IDs to fetch (e.g., ["301851P"])
- `pricing`: Pricing object (can be empty {})

**Response:** JSON object with offer details

### POST `/related-products`

Fetches related products for a given product.

**Request (JSON):**

- `productId`: Product EAN (e.g., "6410405078872")
- `storeId`: Store identifier (e.g., "N110")
- `segmentId`: Segment identifier (default: 1565)

**Response:** JSON object with related products

### POST `/stores-search`

Searches for K-Ruoka store locations.

**Request (JSON):**

- `query`: Search query string (optional)
- `offset`: Pagination offset (default: 0)
- `limit`: Results limit (default: 2000)

**Response:** JSON array of matching store locations

### POST `/product-search`

Searches for products by keyword.

**Request (JSON):**

- `query`: Search string (required)
- `storeId`: Store identifier (required)
- `language`: Language code (default: "fi")
- `offset`: Pagination offset (default: 0)
- `limit`: Results limit (default: 100)
- `discountFilter`: Filter discounted products (default: false)
- `isTosTrOffer`: Filter TOS/TR offers (default: false)

**Response:** JSON object with search results

### POST `/search-offers`

Searches offers by category path using the GET search-offers API. With an empty `categoryPath`, returns ALL offers for a store.

**Request (JSON):**

- `storeId`: Store identifier (required)
- `categoryPath`: Category slug (optional, empty = all offers)
- `offset`: Pagination offset (default: 0)
- `language`: Language code (default: "fi")

**Response:** `{"totalHits": int, "storeId": str, "results": [...], "categoryName": str, "suggestions": [...]}`

### GET `/health`

Validates that the K-Ruoka API is reachable via the browser transport.

**Response:** `{"ok": true/false, "storesStatus": 200, "searchOffersStatus": 200, "errors": []}`

Returns 200 when all checks pass, 503 when something is broken.

### GET `/bulk/stores`

Returns all K-Ruoka stores in a single call.

**Response:** `{"stores": [...], "count": int}`

### POST `/bulk/store-categories`

Returns all offer categories for a store.

**Request (JSON):**

- `storeId`: Store identifier (e.g., "N110")

**Response:** `{"storeId": "N110", "categories": [...], "count": int}`

### POST `/bulk/category-offers`

Returns all offers for a single category in a store, handling pagination internally.

**Request (JSON):**

- `storeId`: Store identifier (e.g., "N110")
- `categorySlug`: Category slug (e.g., "juomat")

**Response:** `{"storeId": "N110", "category": "juomat", "totalHits": int, "offers": [...], "apiCalls": int, "elapsedSeconds": float}`

### POST `/bulk/store-offers`

Returns ALL offers for a store as a flat list using the search-offers endpoint. This is the **preferred** endpoint for syncing — 43% fewer API calls than the category-by-category approach.

**Request (JSON):**

- `storeId`: Store identifier (e.g., "N110")

**Response:**

```json
{
  "storeId": "N110",
  "totalHits": 1264,
  "offers": [...],
  "apiCalls": 27,
  "elapsedSeconds": 14.2
}
```

### POST `/bulk/store-offers-by-category`

Returns ALL offers for a store grouped by category (old approach). Slower but provides category-level breakdown.

**Request (JSON):**

- `storeId`: Store identifier (e.g., "N110")

**Response:**

```json
{
  "storeId": "N110",
  "categories": [
    {"category": "slug", "totalHits": 110, "offers": [...], "apiCalls": 5, "elapsedSeconds": 3.2}
  ],
  "totalOffers": 928,
  "totalApiCalls": 60,
  "totalElapsedSeconds": 35.4
}
```

## K-Ruoka API Constraints

Discovered via benchmarking (see `scripts/full_sweep.py` and `scripts/discover_all.py`):

- **1,060 stores** total
- **344,203 total offers** across all stores
- **~26-27 categories** per store
- **Offers per store:** min 0, max 1,650, mean 325, median 200
- **search-offers page size: 48** (returns up to 48 per request)
- **offer-category max page size: 25** (API returns 400 for limit > 25)
- **Rate limiting:** implicit — 0.5s delays between calls work reliably
- **Full sync estimate (search-offers):** ~8,747 API calls, ~146 min sequential
- **Full sync estimate (offer-category, old):** ~15,343 API calls, ~256 min sequential
- **search-offers saves 43% of API calls** compared to category-by-category

### Store chains

| Chain         | Stores | Total Offers | Avg Offers/Store |
| ------------- | ------ | ------------ | ---------------- |
| K-Market      | 722    | 129,629      | 180              |
| K-Supermarket | 254    | 125,206      | 493              |
| K-Citymarket  | 84     | 89,368       | 1,064            |

### Recommended sync strategy

1. **Job 1:** `GET /health` → validate transport, `GET /bulk/stores` → save all stores
2. **Job N:** `POST /bulk/store-offers` with one storeId per invocation (~8-36s each)
3. Fan out across multiple invocations — each store fits within 1 minute

## Setup

### Prerequisites

- Python 3.12+
- Google Chrome installed
- Virtual environment

### Installation

1. Create and activate virtual environment:

```bash
python -m venv venv
.\venv\Scripts\Activate.ps1  # Windows PowerShell
source venv/bin/activate      # Unix/macOS
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Running Locally

```bash
flask --app scraper.py run --debug
```

The service will start at `http://localhost:5000`

On the first API call, Chrome will launch. If Cloudflare shows a challenge, click "Verify you are human" in the Chrome window.

## Scripts

Quick scripts to call each endpoint locally (server must be running):

```bash
python scripts/offer_categories.py              # default store N110
python scripts/offer_category.py N110 lihat 10   # custom slug & limit
python scripts/fetch_offers.py N110 301851P      # fetch specific offer
python scripts/stores_search.py Helsinki 5       # search stores
python scripts/related_products.py 6410405078872 # related products
python scripts/health_check.py                   # health check
python scripts/bulk_stores.py                    # all stores
python scripts/bulk_store_offers.py N110         # all offers for a store
python scripts/search_offers.py N110 juomat      # search offers by category
python scripts/full_sweep.py 3                   # sweep N stores (default: all)
```

**Note:** Before reporting that the work is done, test the code using the scripts in the `scripts/` directory.

## Dependencies

- **Flask** - Web framework
- **DrissionPage** - Chrome automation via DevTools Protocol (bypasses Cloudflare)
- **gunicorn** - Production WSGI server
- **requests** - HTTP client (for scripts that call Flask endpoints)
- **pytest** - Test framework
