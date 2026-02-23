#!/usr/bin/env python3
"""
Test sync_to_supabase with only 5 stores (not full Helsinki set).
Run with: python scripts/test_sync_5_stores.py
Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in environment (or .env file).
"""
import os
import sys
import time

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_to_supabase import (
    _now_iso,
    upsert_stores,
    sync_store_offers,
    logger,
)
from helpers import fetch_helsinki_stores, close_browser
from supabase import create_client
import atexit

atexit.register(close_browser)

MAX_STORES = 5


def main():
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    supabase = create_client(supabase_url, supabase_key)
    logger.info("Supabase client initialised (%s)", supabase_url)

    sync_time = _now_iso()
    t_start = time.perf_counter()

    # 1. Fetch stores
    logger.info("Fetching Helsinki-area stores...")
    stores = fetch_helsinki_stores()
    logger.info("Found %d total Helsinki stores, using first %d", len(stores), MAX_STORES)
    stores = stores[:MAX_STORES]

    # 2. Upsert stores
    upsert_stores(supabase, stores)

    # 3. Sync offers
    total = 0
    errors = []
    for idx, store in enumerate(stores, 1):
        sid = store["id"]
        logger.info("--- [%d/%d] Syncing %s (%s) ---", idx, len(stores), sid, store.get("name", ""))
        try:
            count = sync_store_offers(supabase, sid, sync_time)
            total += count
        except Exception:
            logger.error("Store %s FAILED", sid, exc_info=True)
            errors.append(sid)

    elapsed = time.perf_counter() - t_start
    logger.info("=" * 60)
    logger.info("Test sync complete")
    logger.info("  Stores: %d", len(stores))
    logger.info("  Total offers: %d", total)
    logger.info("  Errors: %d %s", len(errors), errors)
    logger.info("  Elapsed: %.1fs", elapsed)
    logger.info("=" * 60)

    # 4. Quick verification — count offers for each store in DB
    logger.info("Verifying DB rows...")
    for store in stores:
        db_id = f"k-ruoka:{store['id']}"
        resp = supabase.table("offers").select("id", count="exact").eq("store_id", db_id).execute()
        db_count = resp.count if resp.count is not None else len(resp.data or [])
        logger.info("  %s (%s): %d offers in DB", db_id, store.get("name", ""), db_count)

        # Spot check: look at a couple of offers for batch/categories
        sample = (
            supabase.table("offers")
            .select("id, title, price, normal_price, quantity_required, raw_categories, unit_price, unit")
            .eq("store_id", db_id)
            .gt("quantity_required", 1)
            .limit(3)
            .execute()
        )
        if sample.data:
            logger.info("    Batch offers (quantity_required > 1):")
            for row in sample.data:
                logger.info(
                    "      %s | %s | price=%.2f normal=%.2f qty=%d unit_price=%s",
                    row["id"], row["title"],
                    row.get("price") or 0, row.get("normal_price") or 0,
                    row["quantity_required"], row.get("unit_price"),
                )

        # Check reversed categories
        cat_sample = (
            supabase.table("offers")
            .select("id, raw_categories")
            .eq("store_id", db_id)
            .not_.is_("raw_categories", "null")
            .limit(1)
            .execute()
        )
        if cat_sample.data:
            cats = cat_sample.data[0].get("raw_categories", [])
            if cats:
                logger.info("    Category order (should be leaf→top): %s", [c["name"] for c in cats])


if __name__ == "__main__":
    main()
