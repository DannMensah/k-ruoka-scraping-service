"""
Discovery script: fetches all stores → all offer categories per store →
counts offers per category, tracks API calls, timing, and rate-limit headers.

Usage:
    python scripts/discover_all.py [max_stores]

Calls the K-Ruoka API directly (not via Flask) to measure raw timings.
"""
import sys, json, time
from pathlib import Path

# Add project root so we can import helpers
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from helpers import search_stores, fetch_offer_categories, fetch_offer_category

MAX_STORES = int(sys.argv[1]) if len(sys.argv) > 1 else 5  # default: sample 5 stores
CATEGORY_PAGE_SIZE = 25  # API max is 25
DELAY_BETWEEN_CALLS = 1.0  # seconds between API calls to avoid rate limiting

stats = {
    "api_calls": 0,
    "timings": [],          # (endpoint, seconds)
    "rate_limit_headers": [],
    "stores": [],
}


def timed_call(label, fn, *args, retries=2, **kwargs):
    """Call fn, record timing and bump api_calls counter. Retries on failure."""
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
            elapsed = time.perf_counter() - t0
            stats["api_calls"] += 1
            stats["timings"].append((label, round(elapsed, 3)))
            return result
        except Exception as e:
            elapsed = time.perf_counter() - t0
            if attempt < retries:
                wait = DELAY_BETWEEN_CALLS * (attempt + 2)
                print(f"    [retry {attempt+1}] {label} failed ({e}), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise


def main():
    print(f"=== K-Ruoka API Discovery (max {MAX_STORES} stores) ===\n")

    # ---- 1. Fetch all stores ----
    print("[1/3] Fetching all stores...")
    stores_resp = timed_call("stores/search", search_stores, query="", limit=2000)
    stores = stores_resp if isinstance(stores_resp, list) else stores_resp.get("results", stores_resp.get("stores", []))
    print(f"  → {len(stores)} stores found\n")

    # Sample stores
    sampled = stores[:MAX_STORES]

    total_categories = 0
    total_offers = 0
    category_details = []

    for idx, store in enumerate(sampled):
        store_id = store.get("id") or store.get("storeId") or store.get("slug")
        store_name = store.get("name", store_id)
        print(f"[2/3] Store {idx+1}/{len(sampled)}: {store_name} ({store_id})")

        # ---- 2. Categories for this store ----
        cat_resp = timed_call(f"offer-categories/{store_id}", fetch_offer_categories, store_id=store_id)
        categories = cat_resp.get("offerCategories", [])
        print(f"  → {len(categories)} categories")
        total_categories += len(categories)

        for cat_idx, cat in enumerate(categories):
            slug = cat.get("slug", "")
            kind = "productCategory"  # always required, not returned in category objects
            count_from_listing = cat.get("count", 0)

            # Delay between API calls
            time.sleep(DELAY_BETWEEN_CALLS)

            # ---- 3. First page to learn totalHits ----
            try:
                page_resp = timed_call(
                    f"offer-category/{store_id}/{slug}",
                    fetch_offer_category,
                    store_id=store_id,
                    category={"kind": kind, "slug": slug},
                    offset=0,
                    limit=CATEGORY_PAGE_SIZE,
                )
            except Exception as e:
                print(f"    ✗ {slug}: FAILED ({e})")
                continue
            total_hits = page_resp.get("totalHits", 0)
            offers_in_page = len(page_resp.get("offers", []))
            paginated_ids = page_resp.get("paginatedOfferIds", [])

            # Calculate how many additional pages we'd need
            remaining = max(0, total_hits - CATEGORY_PAGE_SIZE)
            extra_pages = (remaining + CATEGORY_PAGE_SIZE - 1) // CATEGORY_PAGE_SIZE if remaining > 0 else 0

            total_offers += total_hits

            detail = {
                "storeId": store_id,
                "category": slug,
                "countFromListing": count_from_listing,
                "totalHits": total_hits,
                "offersInFirstPage": offers_in_page,
                "paginatedOfferIds": len(paginated_ids),
                "extraPagesNeeded": extra_pages,
                "totalApiCallsForCategory": 1 + extra_pages,  # 1 for first page + extras
            }
            category_details.append(detail)
            print(f"    • {slug}: {total_hits} offers (count={count_from_listing}, {1 + extra_pages} call(s))")

        # Small delay between stores to be polite
        if idx < len(sampled) - 1:
            time.sleep(0.5)

    # ---- Summary ----
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Stores found:          {len(stores)}")
    print(f"Stores sampled:        {len(sampled)}")
    print(f"Total categories:      {total_categories}")
    print(f"Total offers (sampled):{total_offers}")
    print(f"API calls made:        {stats['api_calls']}")

    # Timing analysis
    times = [t for _, t in stats["timings"]]
    if times:
        print(f"\nTiming (seconds):")
        print(f"  Min:    {min(times):.3f}")
        print(f"  Max:    {max(times):.3f}")
        print(f"  Mean:   {sum(times)/len(times):.3f}")
        print(f"  Total:  {sum(times):.3f}")

    # Estimate full run
    if sampled:
        avg_cats = total_categories / len(sampled)
        avg_calls_per_cat = (
            sum(d["totalApiCallsForCategory"] for d in category_details) / len(category_details)
            if category_details else 1
        )
        est_calls = 1 + len(stores) * (1 + avg_cats * avg_calls_per_cat)
        avg_time_per_call = sum(times) / len(times)
        est_time_sequential = est_calls * avg_time_per_call

        print(f"\n--- Full-run estimate (all {len(stores)} stores) ---")
        print(f"  Avg categories/store:      {avg_cats:.1f}")
        print(f"  Avg API calls/category:    {avg_calls_per_cat:.1f}")
        print(f"  Estimated total API calls: {est_calls:.0f}")
        print(f"  Estimated time (sequential): {est_time_sequential:.0f}s ({est_time_sequential/60:.1f}min)")
        print(f"  Avg time/call:             {avg_time_per_call:.3f}s")

    # Dump detailed data
    output = {
        "summary": {
            "totalStores": len(stores),
            "storesSampled": len(sampled),
            "totalCategories": total_categories,
            "totalOffers": total_offers,
            "apiCallsMade": stats["api_calls"],
            "totalTimeSeconds": round(sum(times), 3),
        },
        "categoryDetails": category_details,
        "timings": stats["timings"],
    }
    out_path = Path(__file__).resolve().parent.parent / "examples" / "discovery-results.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDetailed results written to {out_path}")


if __name__ == "__main__":
    main()
