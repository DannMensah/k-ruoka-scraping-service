# K-Ruoka Scraping Service — Implementation Plan

> **Rule**: Refer to this plan after every completed step and after memory compaction.
> Do NOT finish until every checkbox below is checked.

## Current Sprint — Offer Quality & Compound Offer Support

### Step 1: Skip offers where `availability.store` is `false`

- [x] In `map_offer()`, after extracting `product`, check `product.get("availability", {}).get("store")`. If explicitly `False`, return `(None, None)`.
- [x] In `sync_store_offers()` loop, skip the offer when `map_offer` returns `(None, None)`.

### Step 2: Skip offers where price == normal price (no actual discount)

- [x] In `map_offer()`, after extracting `price` and `normal_price`, if both are not None and `price >= normal_price`, return `(None, None)`.

### Step 3: Handle compound/multi-product offers

- [x] Detection: in `sync_store_offers()`, identify compound offers as those where `raw_offer.get("product")` is falsy (no product wrapper in offer-category listing).
- [x] For detected compound offers, call `fetch_offers(store_id, [offer_id])` from helpers.py to get the detailed response with `products` (plural) array.
- [x] Create `map_compound_product()` function that creates an offer row per-product using: product's own EAN, category tree, mobilescan pricing, availability, images — combined with parent offer's pricing, title, validity. Offer ID: `k-ruoka:{store_id}:{offer_id}:{ean}`.
- [x] Skip the parent compound offer itself (don't add to `offer_rows`).
- [x] Each child offer inherits `quantity_required` from `mobilescan.pricing.batch.amount`.
- [x] Respect availability check (step 1) per individual product.
- [x] Import `fetch_offers` from helpers.py.
- [x] Rate limiting: each `fetch_offers` call uses existing global rate limiter via `_post()`.

### Step 4: Batch offer unit price from `mobilescan.pricing.batch`

- [x] In `map_offer()`, when `ms_pricing` has `batch` but no `discount`, extract unit price from `batch.unitPrice.value`.
- [x] Also extract `valid_from`/`valid_to` from `batch.startDate`/`batch.endDate` when `discount` is absent.

### Step 5: Reverse K-Ruoka `raw_categories` order

- [x] In `map_offer()` and `map_compound_product()`, after building `raw_categories` from `category.tree`, reverse the list (leaf → top, most specific first).

### Step 5.5: Flask cleanup

- [x] Removed `scraper.py` (Flask app — no longer used, service runs via GitHub Actions).
- [x] Removed `render.yaml` (Render.com deployment config — no longer used).
- [x] Removed Flask from `requirements.txt`.
- [x] Removed Flask endpoint tests from `tests/test_service.py`.
- [x] Updated `AGENTS.md` to reflect current architecture.

### Step 6: Test with 5 stores

- [ ] Run `sync_to_supabase.py` for 5 stores (not full Helsinki set).
- [ ] Verify DB rows: availability-filtered, price-filtered, compound offers expanded, batch quantities correct, reversed categories.
- [ ] Check logs for skipped offers.

---

## Previous Plan (completed)

### Key Numbers (estimated after Helsinki filter)

| Metric                       | Estimate                           |
| ---------------------------- | ---------------------------------- |
| Stores (Helsinki 50km)       | ~100–150 (vs 1,060 total)          |
| Offers                       | ~30,000–60,000 (vs 344,203 total)  |
| Time per sync                | ~15–40 min (single sequential job) |
| GitHub Actions minutes/month | Unlimited (public repo)            |

---

## Phase 1 — K-Ruoka Service Refactoring

### 1.1 Replace DrissionPage with Patchright in `helpers.py`

- Remove DrissionPage imports and `ChromiumPage` code
- Add Patchright (sync API) as the browser transport
- Replace `_get_browser()` with `_get_context()` using Patchright's `sync_playwright().start()` → `browser.launch_persistent_context(user_data_dir, channel="chrome", headless=False)`
- Replace `_wait_for_cloudflare()` with a Patchright version (polls `page.title()`)
- Replace `_js_fetch(method, url, body)` to use `page.evaluate(js)` (Patchright equivalent)
- Add Linux Chrome detection path (`/usr/bin/google-chrome-stable`) alongside Windows paths
- `_FetchResponse` wrapper stays as-is

### 1.2 Add Helsinki geo-filtering to `helpers.py`

- Add `haversine(lat1, lon1, lat2, lon2)` function returning distance in km
- Add constants: `HELSINKI_LAT = 60.1699`, `HELSINKI_LON = 24.9384`, `MAX_DISTANCE_KM = 50`
- Add `filter_stores_by_distance(stores, lat, lon, max_km)` that filters using `store["geo"]["lat"]` and `store["geo"]["lon"]`
- Add `fetch_helsinki_stores()` that calls `fetch_all_stores()` then filters

### 1.3 Update `full_sweep.py` for Helsinki filtering

- Import the new geo-filter functions
- Add `--helsinki` flag (default on) that applies the distance filter
- Print filtered store count vs total before starting the sweep

### 1.4 Create `sync_to_supabase.py`

- Standalone script — the core of the GitHub Actions job
- Uses `supabase` Python client for DB writes
- Reads `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from environment
- Flow:
  1. Call `fetch_helsinki_stores()` → filtered store list
  2. Upsert stores into `stores` table with ID format `k-ruoka:{storeId}`
  3. For each store, call `search_all_offers_for_store()` → all offers
  4. Map K-Ruoka offers to food-vibe `offers` schema
  5. Extract unique products by EAN → upsert into `products` table
  6. Build EAN→product UUID map → set `canonical_product_id`
  7. Upsert offers → delete stale offers

### 1.5 Update `requirements.txt`

- Remove: `DrissionPage`, `gunicorn`
- Add: `patchright`, `supabase`
- Keep: `Flask` (local dev), `requests` (scripts), `pytest`

---

## Phase 2 — GitHub Actions Workflow

### 2.1 Create `.github/workflows/sync-k-ruoka.yml`

Triggers:

- `repository_dispatch` (type: `sync-k-ruoka`) — triggered by Vercel cron
- `workflow_dispatch` — manual trigger
- `schedule` — every 4 hours as backup (`0 2,6,10,14,18,22 * * *`)

Job steps:

1. Checkout code
2. Set up Python 3.12
3. Install Patchright + browsers (`patchright install chromium`)
4. Start Xvfb (virtual framebuffer for headed Chrome)
5. `pip install -r requirements.txt`
6. Restore `.chrome-profile` from `actions/cache` (persists CF cookies)
7. Run `sync_to_supabase.py`
8. Save `.chrome-profile` to cache

Secrets: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
Timeout: 120 minutes

### 2.2 Update `.gitignore`

- Add `.chrome-profile/`, `__pycache__/`, `.env`, `venv/`, `*.pyc`

---

## Phase 3 — Food-Vibe Integration

### 3.1 Add K-Ruoka source to `lib/sources/config.ts`

- Add entry: `{ id: 'k-ruoka', name: 'K-Ruoka', color: '#FF6600', domain: 'k-ruoka.fi' }`

### 3.2 Create `app/api/cron/update-k-ruoka/route.ts`

- Vercel cron handler
- Validates `CRON_SECRET` bearer token
- POSTs to GitHub API `repository_dispatch` to trigger the Action
- Uses `GITHUB_PAT` env var
- Returns immediately (fire-and-forget, <1s)

### 3.3 Update `vercel.json`

- Add K-Ruoka cron schedule: every 4 hours (`0 2,6,10,14,18,22 * * *`)

### 3.4 Update `lib/products/sync.ts`

- Add `case 'k-ruoka'` in the `switch(source)` block
- Since K-Ruoka offers are written directly by the Python script, this is a no-op that returns 0
- Prevents `syncAllOffers()` from erroring on K-Ruoka stores

---

## Phase 4 — Testing & Verification

### 4.1 Local test: Run modified `full_sweep.py` with Helsinki filter

- Verify store count (~100–150), offer distribution, estimated time

### 4.2 Local test: Run `sync_to_supabase.py` for a small subset

- Set env vars, run for 3 stores, verify Supabase data

### 4.3 GitHub Actions test

- Push code, trigger `workflow_dispatch`, monitor job logs

### 4.4 Food-Vibe build/lint

- `npm run lint` and `npm run build` must pass

### 4.5 Integration test

- Verify K-Ruoka offers appear in discounts page

---

## Data Mapping: K-Ruoka → Food-Vibe Schema

### Stores

| Food-Vibe Column | K-Ruoka Source              |
| ---------------- | --------------------------- |
| `id`             | `k-ruoka:{store.id}`        |
| `remote_id`      | `store.id` (e.g., `"N110"`) |
| `source`         | `"k-ruoka"`                 |
| `name`           | `store.name`                |
| `slug`           | `store.slug`                |
| `brand`          | `store.chainName`           |
| `street_address` | `store.location.address`    |
| `postcode`       | `store.location.postalCode` |
| `city`           | `store.location.city`       |
| `latitude`       | `store.geo.lat`             |
| `longitude`      | `store.geo.lon`             |
| `is_active`      | `True`                      |
| `last_seen_at`   | `now()`                     |
| `raw_data`       | Full store JSON             |

### Offers

| Food-Vibe Column    | K-Ruoka Source                                                            |
| ------------------- | ------------------------------------------------------------------------- |
| `id`                | `k-ruoka:{storeId}:{offer.id}`                                            |
| `store_id`          | `k-ruoka:{storeId}`                                                       |
| `title`             | `offer.localizedTitle.finnish` (fallback: english)                        |
| `price`             | `offer.pricing.price`                                                     |
| `normal_price`      | `offer.normalPricing.price`                                               |
| `unit_price`        | `mobilescan.pricing.discount.unitPrice.value` or `normal.unitPrice.value` |
| `unit`              | Unit from mobilescan pricing                                              |
| `ean`               | `offer.product.product.ean`                                               |
| `image_url`         | `offer.product.product.images[0]` or `offer.image`                        |
| `source_url`        | `https://www.k-ruoka.fi/kauppa/tuote/{ean}`                               |
| `valid_from`        | `mobilescan.pricing.discount.startDate`                                   |
| `valid_to`          | `mobilescan.pricing.discount.endDate`                                     |
| `raw_categories`    | Mapped from `product.category.tree`                                       |
| `quantity_required` | `mobilescan.pricing.batch.amount` or `1`                                  |

### Products

| Food-Vibe Column | K-Ruoka Source                    |
| ---------------- | --------------------------------- |
| `ean`            | `offer.product.product.ean`       |
| `name`           | `offer.localizedTitle.finnish`    |
| `image_url`      | `offer.product.product.images[0]` |

---

## Manual Steps Already Completed

- [x] Created public GitHub repo `DannMensah/k-ruoka-scraping-service`
- [x] Initial push done
- [x] Added `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as GitHub Actions secrets
- [x] Added `GITHUB_PAT` to Vercel deployment and redeployed
