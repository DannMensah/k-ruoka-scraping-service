"""
Full sweep: use search-offers to get total offer count for stores.

By default, only stores within 50km of Helsinki are included (--helsinki flag).
Use --all to scan all 1,060+ stores instead.

Usage:
    python scripts/full_sweep.py                # Helsinki-area stores only
    python scripts/full_sweep.py --all          # all stores
    python scripts/full_sweep.py --all 100      # first 100 of all stores
    python scripts/full_sweep.py 50             # first 50 Helsinki-area stores
"""
import sys
import json
import time
import math
import statistics
import atexit
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from helpers import (
    fetch_all_stores,
    fetch_helsinki_stores,
    search_offers,
    close_browser,
    DELAY_BETWEEN_CALLS,
    SEARCH_OFFERS_PAGE_SIZE,
    HELSINKI_LAT,
    HELSINKI_LON,
    MAX_DISTANCE_KM,
)

# Shut down Chrome when the script exits
atexit.register(close_browser)

# Parse args: --all flag and optional max-stores number
USE_HELSINKI_FILTER = "--all" not in sys.argv
args_numbers = [a for a in sys.argv[1:] if a != "--all" and a.lstrip("-").isdigit()]
MAX_STORES = int(args_numbers[0]) if args_numbers else None
PAGE_SIZE = SEARCH_OFFERS_PAGE_SIZE  # 48

# ---------------------------------------------------------------------------
# Error tracking — halt on too many consecutive errors
# ---------------------------------------------------------------------------
MAX_CONSECUTIVE_ERRORS = 5
ERROR_RATE_THRESHOLD = 0.20  # halt if >20% of calls fail


def pages_needed(offer_count: int) -> int:
    """How many API calls to paginate through all offers in a category."""
    if offer_count <= 0:
        return 0
    return math.ceil(offer_count / PAGE_SIZE)


def percentile(data: list, p: int) -> float:
    """p-th percentile of a sorted list."""
    if not data:
        return 0
    k = (len(data) - 1) * p / 100
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return data[int(k)]
    return data[f] * (c - k) + data[c] * (k - f)


def print_histogram(values: list, label: str, bins: list[tuple[int, int]]):
    """Print a simple text histogram."""
    counts = []
    for lo, hi in bins:
        n = sum(1 for v in values if lo <= v <= hi)
        counts.append((lo, hi, n))
    max_bar = max(c for _, _, c in counts) if counts else 1
    print(f"\n  {label} distribution:")
    for lo, hi, n in counts:
        bar = "█" * max(1, round(40 * n / max_bar)) if n else ""
        pct = 100 * n / len(values) if values else 0
        if hi == float("inf"):
            print(f"    {lo:>5}+    : {n:>5} ({pct:5.1f}%) {bar}")
        else:
            print(f"    {lo:>5}-{hi:<5}: {n:>5} ({pct:5.1f}%) {bar}")


def main():
    t0_total = time.perf_counter()

    # ---- 1. Fetch stores ----
    if USE_HELSINKI_FILTER:
        print(f"Fetching stores within {MAX_DISTANCE_KM}km of Helsinki ({HELSINKI_LAT}, {HELSINKI_LON})...")
        stores = fetch_helsinki_stores()
        print(f"  → {len(stores)} stores in Helsinki area\n")
    else:
        print("Fetching all stores...")
        stores = fetch_all_stores()
        print(f"  → {len(stores)} stores\n")

    if MAX_STORES:
        stores = stores[:MAX_STORES]
        print(f"  (limited to first {MAX_STORES} stores)\n")

    # ---- 2. For each store, call search_offers("", ...) to get totalHits ----
    store_data = []
    errors = []
    api_calls = 1  # 1 for the stores call
    consecutive_errors = 0

    for idx, store in enumerate(stores):
        store_id = store.get("id", "")
        store_name = store.get("name", store_id)
        chain = store.get("chain", "?")

        if idx % 50 == 0 or idx == len(stores) - 1:
            elapsed = time.perf_counter() - t0_total
            eta = (elapsed / (idx + 1)) * (len(stores) - idx - 1) if idx > 0 else 0
            error_rate = len(errors) / (idx + 1) if idx > 0 else 0
            print(
                f"  [{idx+1}/{len(stores)}] {store_name} ({store_id})  "
                f"[{elapsed:.0f}s elapsed, ~{eta:.0f}s remaining, "
                f"{len(errors)} errors ({error_rate:.1%})]"
            )

        time.sleep(DELAY_BETWEEN_CALLS)
        api_calls += 1

        try:
            resp = search_offers(store_id=store_id, category_path="", offset=0)
            total_hits = resp.get("totalHits", 0)
            first_page_count = len(resp.get("results", []))
            consecutive_errors = 0  # reset on success
        except Exception as e:
            consecutive_errors += 1
            errors.append({"storeId": store_id, "error": str(e), "index": idx})

            # Check halt conditions
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(
                    f"\n*** HALTING: {consecutive_errors} consecutive errors. "
                    f"Last error: {e}"
                )
                print(f"*** Processed {idx+1}/{len(stores)} stores before halt.")
                break

            error_rate = len(errors) / (idx + 1)
            if idx >= 20 and error_rate > ERROR_RATE_THRESHOLD:
                print(
                    f"\n*** HALTING: error rate {error_rate:.1%} exceeds "
                    f"threshold {ERROR_RATE_THRESHOLD:.0%} after {idx+1} stores."
                )
                break

            total_hits = 0
            first_page_count = 0

        total_pages = pages_needed(total_hits)

        store_data.append({
            "storeId": store_id,
            "name": store_name,
            "chain": chain,
            "totalOffers": total_hits,
            "firstPageCount": first_page_count,
            "totalApiCallsForOffers": total_pages,
        })

    elapsed_total = time.perf_counter() - t0_total

    # ====================================================================
    # ANALYSIS
    # ====================================================================
    print("\n" + "=" * 70)
    print("FULL SWEEP RESULTS  (using search-offers endpoint)")
    print("=" * 70)

    num_stores = len(store_data)
    offer_counts = [s["totalOffers"] for s in store_data]
    page_counts = [s["totalApiCallsForOffers"] for s in store_data]

    # --- Stores overview ---
    print(f"\nStores analysed: {num_stores}")
    print(f"Errors:          {len(errors)}")
    print(f"API calls made:  {api_calls}")
    print(f"Wall time:       {elapsed_total:.0f}s ({elapsed_total/60:.1f}min)")

    if not store_data:
        print("\nNo data to analyse.")
        return

    # --- Chain breakdown ---
    chain_counter = Counter(s["chain"] for s in store_data)
    print(f"\nStores by chain:")
    for chain, count in chain_counter.most_common():
        chain_offers = [s["totalOffers"] for s in store_data if s["chain"] == chain]
        print(
            f"  {chain:<25} {count:>5} stores, "
            f"{sum(chain_offers):>8} total offers, "
            f"avg {sum(chain_offers)/len(chain_offers):.0f}/store"
        )

    # --- Offers per store ---
    offer_counts_sorted = sorted(offer_counts)
    print(f"\nOffers per store:")
    print(f"  Min:    {min(offer_counts)}")
    print(f"  Max:    {max(offer_counts)}")
    print(f"  Mean:   {statistics.mean(offer_counts):.1f}")
    print(f"  Median: {statistics.median(offer_counts):.0f}")
    if len(offer_counts) > 1:
        print(f"  Stdev:  {statistics.stdev(offer_counts):.1f}")
    print(f"  P5:     {percentile(offer_counts_sorted, 5):.0f}")
    print(f"  P25:    {percentile(offer_counts_sorted, 25):.0f}")
    print(f"  P75:    {percentile(offer_counts_sorted, 75):.0f}")
    print(f"  P95:    {percentile(offer_counts_sorted, 95):.0f}")
    print(f"  Total:  {sum(offer_counts)}")

    stores_0_offers = sum(1 for o in offer_counts if o == 0)
    print(f"  Stores with 0 offers: {stores_0_offers}")

    print_histogram(offer_counts, "Offers/store", [
        (0, 0), (1, 50), (51, 100), (101, 200), (201, 400),
        (401, 600), (601, 800), (801, 1000), (1001, 1500),
        (1501, 2000), (2001, float("inf")),
    ])

    # --- API call estimates (using search-offers page size = 48) ---
    total_offer_page_calls = sum(page_counts)
    total_api_calls_sync = 1 + num_stores + total_offer_page_calls

    print(f"\n{'=' * 70}")
    print("API CALL ESTIMATES FOR FULL SYNC  (search-offers, page size 48)")
    print(f"{'=' * 70}")
    print(f"  Stores call:                     1")
    print(f"  Count calls (1/store):           {num_stores}")
    print(f"  Offer page calls (paginate):     {total_offer_page_calls}")
    print(f"  TOTAL API CALLS:                 {total_api_calls_sync}")
    print()

    avg_call_time = 0.5  # slightly higher for browser fetch
    delay = DELAY_BETWEEN_CALLS
    effective_time = avg_call_time + delay

    seq_time = total_api_calls_sync * effective_time
    print(f"  Sequential time estimate:        {seq_time:.0f}s ({seq_time/60:.0f}min, {seq_time/3600:.1f}h)")
    print(f"    (at {avg_call_time}s/call + {delay}s delay)")

    # Also compare with old approach (offer-category, page size 25)
    old_pages = sum(math.ceil(o / 25) if o > 0 else 0 for o in offer_counts)
    old_total = 1 + num_stores + old_pages
    print(f"\n  Comparison with old approach (offer-category, page 25):")
    print(f"    Old total API calls:           {old_total}")
    print(f"    New total API calls:           {total_api_calls_sync}")
    print(f"    Savings:                       {old_total - total_api_calls_sync} calls ({100*(old_total-total_api_calls_sync)/old_total:.0f}%)")

    # Per-store breakdown
    pages_sorted = sorted(page_counts)
    print(f"\n  API calls per store (just offer pages):")
    print(f"    Min:    {min(page_counts)}")
    print(f"    Max:    {max(page_counts)}")
    print(f"    Mean:   {statistics.mean(page_counts):.1f}")
    print(f"    Median: {statistics.median(page_counts):.0f}")
    print(f"    P95:    {percentile(pages_sorted, 95):.0f}")

    per_store_time = [(1 + p) * effective_time for p in page_counts]
    per_store_sorted = sorted(per_store_time)
    print(f"\n  Time per store (count + offer pages):")
    print(f"    Min:    {min(per_store_time):.0f}s")
    print(f"    Max:    {max(per_store_time):.0f}s")
    print(f"    Mean:   {statistics.mean(per_store_time):.0f}s")
    print(f"    Median: {statistics.median(per_store_time):.0f}s")
    print(f"    P95:    {percentile(per_store_sorted, 95):.0f}s")

    stores_over_5min = sum(1 for t in per_store_time if t > 300)
    print(f"    Stores exceeding 5 min: {stores_over_5min}")

    # --- Top 20 heaviest stores ---
    by_offers = sorted(store_data, key=lambda s: s["totalOffers"], reverse=True)
    print(f"\nTop 20 stores by offer count:")
    print(f"  {'Store':<45} {'Chain':<15} {'Offers':>7} {'Pages':>6} {'~Time':>6}")
    print(f"  {'-'*45} {'-'*15} {'-'*7} {'-'*6} {'-'*6}")
    for s in by_offers[:20]:
        t = (1 + s["totalApiCallsForOffers"]) * effective_time
        print(
            f"  {s['name'][:45]:<45} {s['chain'][:15]:<15} "
            f"{s['totalOffers']:>7} {s['totalApiCallsForOffers']:>6} {t:>5.0f}s"
        )

    # --- Bottom 20 ---
    by_offers_asc = sorted(store_data, key=lambda s: s["totalOffers"])
    print(f"\nBottom 20 stores by offer count:")
    print(f"  {'Store':<45} {'Chain':<15} {'Offers':>7}")
    print(f"  {'-'*45} {'-'*15} {'-'*7}")
    for s in by_offers_asc[:20]:
        print(
            f"  {s['name'][:45]:<45} {s['chain'][:15]:<15} "
            f"{s['totalOffers']:>7}"
        )

    # --- Write raw data ---
    output = {
        "summary": {
            "storesAnalysed": num_stores,
            "errors": len(errors),
            "totalOffers": sum(offer_counts),
            "totalApiCallsForFullSync": total_api_calls_sync,
            "estimatedSequentialTimeSeconds": round(seq_time),
            "pageSize": PAGE_SIZE,
            "endpoint": "search-offers",
        },
        "stores": store_data,
        "errors": errors,
    }
    out_path = Path(__file__).resolve().parent.parent / "examples" / "full-sweep-results.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRaw data written to {out_path}")


if __name__ == "__main__":
    main()
