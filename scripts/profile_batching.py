#!/usr/bin/env python3
"""
Profile the sync pipeline for 1-2 stores: fetch, map, batch-expand.

Focuses on identifying processing bottlenecks (mapping, compound expansion).
Does NOT write to Supabase — read-only against K-Ruoka API.

Usage:
    python scripts/profile_batching.py [store_id1] [store_id2]

Requires FLARESOLVERR_URL env var (e.g. http://localhost:8191/v1).
"""
import sys
import os
import time
import atexit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import search_all_offers_for_store, fetch_offers, close_browser

# Import mapping functions from sync script
from sync_to_supabase import (
    map_offer,
    map_compound_product,
    _is_compound_offer,
    _chunked,
    COMPOUND_FETCH_BATCH,
)

atexit.register(close_browser)

STORE_IDS = sys.argv[1:] if len(sys.argv) > 1 else ["N110", "N111"]


def profile_store(store_id: str) -> dict:
    """Profile the full pipeline for one store. Returns timing breakdown."""
    print(f"\n{'='*60}")
    print(f"Store {store_id}")
    print(f"{'='*60}")

    # ---- Phase 1: Fetch all offers ----
    t0 = time.perf_counter()
    result = search_all_offers_for_store(store_id)
    t_fetch = time.perf_counter() - t0
    offers = result.get("offers", [])
    print(f"\n[FETCH] {len(offers)} offers in {t_fetch:.2f}s "
          f"({result.get('apiCalls', '?')} API calls)")

    if not offers:
        print("  No offers — skipping.")
        return {"store_id": store_id, "total_offers": 0}

    # ---- Phase 2: First pass — map regular offers, collect compound IDs ----
    t1 = time.perf_counter()
    regular_rows = []
    compound_ids = []
    skipped = 0

    for raw in offers:
        if _is_compound_offer(raw):
            oid = raw.get("id")
            if oid:
                compound_ids.append(oid)
            continue
        row, prod = map_offer(store_id, raw)
        if row is None:
            skipped += 1
            continue
        regular_rows.append((row, prod))

    t_map_regular = time.perf_counter() - t1
    print(f"\n[MAP REGULAR] {len(regular_rows)} mapped, {skipped} skipped "
          f"in {t_map_regular*1000:.1f}ms")
    if regular_rows:
        per_offer_us = (t_map_regular / len(regular_rows)) * 1_000_000
        print(f"  {per_offer_us:.1f} \u00b5s/offer")

    # ---- Phase 3: Batch-fetch compound offers ----
    compound_rows = []
    compound_skipped = 0
    n_batches = (len(compound_ids) + COMPOUND_FETCH_BATCH - 1) // COMPOUND_FETCH_BATCH if compound_ids else 0
    total_fetch = 0
    total_map = 0

    if compound_ids:
        print(f"\n[COMPOUND] {len(compound_ids)} compound offers \u2192 "
              f"{n_batches} batch call(s) (batch_size={COMPOUND_FETCH_BATCH})")

        t2 = time.perf_counter()
        fetch_times = []
        map_times = []

        for batch_ids in _chunked(compound_ids, COMPOUND_FETCH_BATCH):
            # Time the API call
            t_call = time.perf_counter()
            detail = fetch_offers(store_id, batch_ids)
            t_call_elapsed = time.perf_counter() - t_call
            fetch_times.append(t_call_elapsed)

            detail_offers = detail.get("offers", [])

            # Time the mapping
            t_map = time.perf_counter()
            for detail_offer in detail_offers:
                products_list = detail_offer.get("products", [])
                for pw in products_list:
                    o_row, p_row = map_compound_product(store_id, detail_offer, pw)
                    if o_row is None:
                        compound_skipped += 1
                        continue
                    compound_rows.append((o_row, p_row))
            t_map_elapsed = time.perf_counter() - t_map
            map_times.append(t_map_elapsed)

            print(f"  batch({len(batch_ids)} IDs): "
                  f"fetch={t_call_elapsed:.2f}s, "
                  f"map={t_map_elapsed*1000:.1f}ms, "
                  f"\u2192 {len(detail_offers)} offers, "
                  f"{sum(len(o.get('products',[])) for o in detail_offers)} products")

        t_compound_total = time.perf_counter() - t2
        total_fetch = sum(fetch_times)
        total_map = sum(map_times)

        print(f"\n  COMPOUND TOTAL: {t_compound_total:.2f}s "
              f"(fetch={total_fetch:.2f}s, map={total_map*1000:.1f}ms)")
        print(f"  {len(compound_rows)} mapped, {compound_skipped} skipped")
        if compound_rows:
            per_compound_us = (total_map / len(compound_rows)) * 1_000_000
            print(f"  {per_compound_us:.1f} \u00b5s/compound-product (mapping only)")
    else:
        print("\n[COMPOUND] None found.")
        t_compound_total = 0

    # ---- Summary ----
    total_mapped = len(regular_rows) + len(compound_rows)
    total_time = t_fetch + t_map_regular + (t_compound_total if compound_ids else 0)

    print(f"\n--- Store {store_id} summary ---")
    print(f"  Total offers from API : {len(offers)}")
    print(f"  Regular mapped        : {len(regular_rows)}")
    print(f"  Compound mapped       : {len(compound_rows)}")
    print(f"  Total mapped          : {total_mapped}")
    print(f"  Skipped               : {skipped + compound_skipped}")
    print(f"  Time breakdown:")
    print(f"    Fetch offers        : {t_fetch:.2f}s")
    print(f"    Map regular         : {t_map_regular*1000:.1f}ms")
    if compound_ids:
        print(f"    Compound (total)    : {t_compound_total:.2f}s")
        print(f"      \u2514 fetch API       : {total_fetch:.2f}s")
        print(f"      \u2514 map products    : {total_map*1000:.1f}ms")
    print(f"    TOTAL               : {total_time:.2f}s")

    return {
        "store_id": store_id,
        "total_offers": len(offers),
        "regular_mapped": len(regular_rows),
        "compound_mapped": len(compound_rows),
        "t_fetch": t_fetch,
        "t_map_regular": t_map_regular,
        "t_compound_total": t_compound_total if compound_ids else 0,
    }


def main() -> None:
    print(f"Profiling {len(STORE_IDS)} store(s): {STORE_IDS}")
    print(f"COMPOUND_FETCH_BATCH = {COMPOUND_FETCH_BATCH}")

    results = []
    for sid in STORE_IDS:
        results.append(profile_store(sid))

    print(f"\n{'='*60}")
    print("OVERALL SUMMARY")
    print(f"{'='*60}")
    for r in results:
        sid = r["store_id"]
        if r["total_offers"] == 0:
            print(f"  {sid}: no offers")
            continue
        print(f"  {sid}: {r['total_offers']} offers \u2192 "
              f"{r['regular_mapped']}+{r['compound_mapped']} mapped, "
              f"fetch={r['t_fetch']:.1f}s, "
              f"map_regular={r['t_map_regular']*1000:.0f}ms, "
              f"compound={r['t_compound_total']:.1f}s")


if __name__ == "__main__":
    main()
