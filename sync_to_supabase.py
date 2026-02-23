#!/usr/bin/env python3
"""
Sync K-Ruoka offers (Helsinki area) to Supabase.

This script is designed to run as a GitHub Actions job. It:
1. Fetches all K-Ruoka stores within 50km of Helsinki
2. Upserts stores into the Supabase `stores` table
3. For each store, fetches all offers via the search-offers API
4. Maps K-Ruoka offers to the food-vibe schema
5. Upserts products (by EAN) and offers into Supabase
6. Deletes stale offers (not seen in this sync run)

Environment variables required:
    SUPABASE_URL - Supabase project URL
    SUPABASE_SERVICE_ROLE_KEY - Supabase service role key
"""
import os
import sys
import time
import json
import logging
import atexit
from datetime import datetime, timezone

# Add parent directory to path for helpers import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import (
    fetch_helsinki_stores,
    search_all_offers_for_store,
    fetch_offers,
    close_browser,
)
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Shut down Chrome when the script exits
atexit.register(close_browser)

SOURCE = "k-ruoka"
BATCH_SIZE = 500  # Supabase upsert batch size
COMPOUND_FETCH_BATCH = 25  # Max offer IDs per fetch-offers API call

UNIT_MAP = {
    "kpl": "pcs", "st": "pcs", "pcs": "pcs",
    "kg": "kg", "kg1": "kg",
    "l": "l", "ltr": "l",
    "g": "g", "gr": "g",
    "ml": "ml",
}


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def map_store(store_data: dict) -> dict:
    """Map a K-Ruoka store dict to a Supabase `stores` row.

    K-Ruoka API store fields (discovered from real responses):
        id, name, slug, chainName, geo{latitude, longitude},
        location (string address), openNextTwoDays, ...
    """
    geo = store_data.get("geo", {}) or {}

    # `location` can be a string or a dict — handle both
    location_raw = store_data.get("location")
    if isinstance(location_raw, dict):
        street_address = location_raw.get("address")
        postcode = location_raw.get("postalCode")
        city = location_raw.get("city")
    elif isinstance(location_raw, str):
        street_address = location_raw
        postcode = None
        city = None
    else:
        street_address = None
        postcode = None
        city = None

    return {
        "id": f"k-ruoka:{store_data['id']}",
        "remote_id": store_data["id"],
        "source": SOURCE,
        "name": store_data["name"],
        "slug": store_data.get("slug"),
        "brand": store_data.get("chainName"),
        "street_address": street_address,
        "postcode": postcode,
        "city": city,
        "latitude": geo.get("latitude"),
        "longitude": geo.get("longitude"),
        "is_active": True,
        "last_seen_at": _now_iso(),
        "raw_data": store_data,
    }


def map_unit(raw_unit: str | None) -> str | None:
    """Map a raw unit string (e.g. 'kpl', 'kg') to a standard short code."""
    if not raw_unit:
        return None
    return UNIT_MAP.get(raw_unit.lower().strip())


def _is_compound_offer(offer: dict) -> bool:
    """Return True if the offer has no embedded product (compound/multi-product).

    Compound offers in the offer-category listing lack a `product` field and
    require a separate fetch-offers call to get individual product details.
    """
    return not offer.get("product")


def _extract_product_fields(product: dict, offer: dict) -> dict:
    """Extract common fields from a product dict nested in an offer.

    Shared logic between map_offer (single-product) and map_compound_product.
    Returns a dict with keys: ean, image_url, source_url, raw_categories,
    unit_price, unit, valid_from, valid_to, quantity_required, ms_pricing.
    """
    mobilescan = product.get("mobilescan", {}) or {}
    ms_pricing = mobilescan.get("pricing", {}) or {}

    # ---- unit price / unit (prefer discount, then batch, then normal) ----
    unit_price = None
    unit = None

    discount_up = (ms_pricing.get("discount", {}) or {}).get("unitPrice", {}) or {}
    batch_up = (ms_pricing.get("batch", {}) or {}).get("unitPrice", {}) or {}
    normal_up = (ms_pricing.get("normal", {}) or {}).get("unitPrice", {}) or {}

    if discount_up.get("value") is not None:
        unit_price = discount_up["value"]
        unit = map_unit(discount_up.get("unit"))
    elif batch_up.get("value") is not None:
        unit_price = batch_up["value"]
        unit = map_unit(batch_up.get("unit"))
    elif normal_up.get("value") is not None:
        unit_price = normal_up["value"]
        unit = map_unit(normal_up.get("unit"))

    # ---- EAN ----
    ean = product.get("ean")
    if ean is not None:
        ean = str(ean).strip()
        if not ean:
            ean = None

    # ---- image URL ----
    images = product.get("images") or []
    image_url = images[0] if images else offer.get("image")

    # ---- source URL ----
    url_slug = (product.get("productAttributes", {}) or {}).get("urlSlug")
    if url_slug:
        source_url = f"https://www.k-ruoka.fi/kauppa/tuote/{url_slug}"
    elif ean:
        source_url = f"https://www.k-ruoka.fi/kauppa/tuotehaku?haku={ean}"
    else:
        source_url = None

    # ---- validity dates (prefer discount, fall back to batch) ----
    discount_info = ms_pricing.get("discount", {}) or {}
    batch_info = ms_pricing.get("batch", {}) or {}
    valid_from = discount_info.get("startDate") or batch_info.get("startDate")
    valid_to = discount_info.get("endDate") or batch_info.get("endDate")

    # ---- categories (reversed: leaf → top for UI display) ----
    raw_categories = None
    tree = (product.get("category", {}) or {}).get("tree")
    if tree and isinstance(tree, list):
        raw_categories = list(reversed([
            {
                "name": (entry.get("localizedName", {}) or {}).get("finnish", ""),
                "slug": entry.get("slug", ""),
            }
            for entry in tree
        ]))

    # ---- batch / quantity_required ----
    batch = ms_pricing.get("batch", {}) or {}
    quantity_required = batch.get("amount", 1) if batch.get("amount") else 1

    return {
        "ean": ean,
        "image_url": image_url,
        "source_url": source_url,
        "raw_categories": raw_categories,
        "unit_price": unit_price,
        "unit": unit,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "quantity_required": quantity_required,
        "ms_pricing": ms_pricing,
    }


def map_offer(store_id: str, offer: dict) -> tuple[dict | None, dict | None]:
    """Map a K-Ruoka offer to an (offer_row, product_row | None) tuple.

    Returns (None, None) when the offer should be skipped:
    - product.availability.store is False
    - price >= normal_price (no actual discount)
    - price is still None after all fallbacks

    Safely navigates all nested fields — missing keys resolve to None.
    """
    offer_id = offer.get("id", "unknown")
    now = _now_iso()

    # ---- title ----
    loc_title = offer.get("localizedTitle", {}) or {}
    title = loc_title.get("finnish") or loc_title.get("english") or "Unknown"

    # ---- top-level pricing ----
    pricing = offer.get("pricing", {}) or {}
    price = pricing.get("price")

    normal_pricing = offer.get("normalPricing", {}) or {}
    normal_price = normal_pricing.get("price")

    # ---- product & mobilescan ----
    product_wrapper = offer.get("product", {}) or {}
    product = product_wrapper.get("product", {}) or {}

    # ---- Price fallback: top-level → discount → batch ----
    # Many offers (esp. Plussa percentage-based) have no top-level price.
    # The real price lives inside mobilescan.pricing.discount or .batch.
    if price is None:
        ms = (product.get("mobilescan", {}) or {}).get("pricing", {}) or {}
        price = (ms.get("discount", {}) or {}).get("price")
        if price is None:
            price = (ms.get("batch", {}) or {}).get("price")
    if normal_price is None:
        ms = (product.get("mobilescan", {}) or {}).get("pricing", {}) or {}
        normal_price = (ms.get("normal", {}) or {}).get("price")

    # ---- Skip if price equals or exceeds normal price (no real discount) ----
    if price is not None and normal_price is not None and price >= normal_price:
        return None, None

    # ---- Skip if price is still None after all fallbacks ----
    if price is None:
        return None, None

    # ---- Skip if product is not available in-store ----
    availability = product.get("availability", {}) or {}
    if availability.get("store") is False:
        return None, None

    fields = _extract_product_fields(product, offer)

    # Fall back EAN to product_wrapper.id (for single-product offers)
    ean = fields["ean"] or product_wrapper.get("id")
    if ean is not None:
        ean = str(ean).strip()
        if not ean:
            ean = None

    offer_row = {
        "id": f"k-ruoka:{store_id}:{offer_id}",
        "store_id": f"k-ruoka:{store_id}",
        "title": title,
        "price": price,
        "unit_price": fields["unit_price"],
        "unit": fields["unit"],
        "normal_price": normal_price,
        "quantity_required": fields["quantity_required"],
        "source_url": fields["source_url"],
        "image_url": fields["image_url"],
        "raw_categories": fields["raw_categories"],
        "valid_from": fields["valid_from"],
        "valid_to": fields["valid_to"],
        "updated_at": now,
        # canonical_product_id is set later after product upsert
    }

    # ---- product row (skip internal EANs starting with '2') ----
    product_row = None
    if ean and not ean.startswith("2"):
        product_row = {
            "ean": ean,
            "name": title,
            "image_url": fields["image_url"],
        }

    return offer_row, product_row


def map_compound_product(
    store_id: str,
    offer: dict,
    product_wrapper: dict,
) -> tuple[dict | None, dict | None]:
    """Map one product from a compound (multi-product) offer.

    Returns (offer_row, product_row | None) or (None, None) if skipped.
    The offer ID includes the EAN to ensure uniqueness per product.
    """
    offer_id = offer.get("id", "unknown")
    now = _now_iso()

    product = product_wrapper.get("product", {}) or {}

    # ---- Skip if product is not available in-store ----
    availability = product.get("availability", {}) or {}
    if availability.get("store") is False:
        return None, None

    # ---- title (prefer product-level name, fall back to offer title) ----
    product_name = (product.get("localizedName", {}) or {}).get("finnish")
    loc_title = offer.get("localizedTitle", {}) or {}
    offer_title = loc_title.get("finnish") or loc_title.get("english") or "Unknown"
    title = product_name or offer_title

    # ---- top-level pricing (from parent offer) ----
    pricing = offer.get("pricing", {}) or {}
    price = pricing.get("price")

    normal_pricing = offer.get("normalPricing", {}) or {}
    normal_price = normal_pricing.get("price")

    # ---- Price fallback: top-level → discount → batch ----
    if price is None:
        ms = (product.get("mobilescan", {}) or {}).get("pricing", {}) or {}
        price = (ms.get("discount", {}) or {}).get("price")
        if price is None:
            price = (ms.get("batch", {}) or {}).get("price")
    if normal_price is None:
        ms = (product.get("mobilescan", {}) or {}).get("pricing", {}) or {}
        normal_price = (ms.get("normal", {}) or {}).get("price")

    # ---- Skip if price equals or exceeds normal price ----
    if price is not None and normal_price is not None and price >= normal_price:
        return None, None

    # ---- Skip if price is still None after all fallbacks ----
    if price is None:
        return None, None

    fields = _extract_product_fields(product, offer)
    ean = fields["ean"]

    if not ean:
        return None, None

    offer_row = {
        "id": f"k-ruoka:{store_id}:{offer_id}:{ean}",
        "store_id": f"k-ruoka:{store_id}",
        "title": title,
        "price": price,
        "unit_price": fields["unit_price"],
        "unit": fields["unit"],
        "normal_price": normal_price,
        "quantity_required": fields["quantity_required"],
        "source_url": fields["source_url"],
        "image_url": fields["image_url"],
        "raw_categories": fields["raw_categories"],
        "valid_from": fields["valid_from"],
        "valid_to": fields["valid_to"],
        "updated_at": now,
    }

    product_row = None
    if not ean.startswith("2"):
        product_row = {
            "ean": ean,
            "name": title,
            "image_url": fields["image_url"],
        }

    return offer_row, product_row


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def _chunked(lst: list, size: int):
    """Yield successive chunks of *size* from *lst*."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def upsert_stores(supabase, stores: list[dict]) -> None:
    """Upsert store rows into Supabase in batches."""
    rows = [map_store(s) for s in stores]
    for batch in _chunked(rows, BATCH_SIZE):
        supabase.table("stores").upsert(batch, on_conflict="id").execute()
    logger.info("Upserted %d stores", len(rows))


def _upsert_products(supabase, product_rows: list[dict]) -> None:
    """Upsert product rows (deduplicated by EAN) in batches."""
    if not product_rows:
        return
    for batch in _chunked(product_rows, BATCH_SIZE):
        supabase.table("products").upsert(batch, on_conflict="ean").execute()


def _fetch_product_ids(supabase, eans: list[str]) -> dict[str, str]:
    """Return a mapping of EAN → product UUID from the products table."""
    if not eans:
        return {}
    ean_to_id: dict[str, str] = {}
    for batch in _chunked(eans, BATCH_SIZE):
        resp = (
            supabase.table("products")
            .select("id, ean")
            .in_("ean", batch)
            .execute()
        )
        for row in resp.data or []:
            ean_to_id[row["ean"]] = row["id"]
    return ean_to_id


def _upsert_offers(supabase, offer_rows: list[dict]) -> None:
    """Upsert offer rows in batches (deduplicated by id within each batch)."""
    if not offer_rows:
        return
    # Deduplicate — keep last occurrence of each id
    seen: dict[str, dict] = {}
    for row in offer_rows:
        seen[row["id"]] = row
    deduped = list(seen.values())
    for batch in _chunked(deduped, BATCH_SIZE):
        supabase.table("offers").upsert(batch, on_conflict="id").execute()


def _delete_stale_offers(supabase, store_db_id: str, sync_time: str) -> int:
    """Delete offers for *store_db_id* whose updated_at is older than *sync_time*.

    Returns the count of deleted rows.
    """
    resp = (
        supabase.table("offers")
        .delete()
        .eq("store_id", store_db_id)
        .lt("updated_at", sync_time)
        .execute()
    )
    return len(resp.data) if resp.data else 0


# ---------------------------------------------------------------------------
# Per-store sync
# ---------------------------------------------------------------------------

def sync_store_offers(supabase, store_id: str, sync_time: str) -> int:
    """Fetch and sync all offers for a single store.

    Args:
        supabase: Supabase client instance.
        store_id: K-Ruoka store ID (e.g. "N110").
        sync_time: ISO timestamp marking the start of this sync run.

    Returns:
        Number of offers synced for this store.
    """
    # 1. Fetch all offers from K-Ruoka
    result = search_all_offers_for_store(store_id)
    offers_raw = result.get("offers", [])
    logger.info(
        "Store %s: fetched %d offers in %.1fs (%d API calls)",
        store_id,
        len(offers_raw),
        result.get("elapsedSeconds", 0),
        result.get("apiCalls", 0),
    )

    if not offers_raw:
        # No offers — still delete stale ones
        deleted = _delete_stale_offers(supabase, f"k-ruoka:{store_id}", sync_time)
        if deleted:
            logger.info("Store %s: deleted %d stale offers", store_id, deleted)
        return 0

    # 2. Map offers
    offer_rows: list[dict] = []
    product_rows_map: dict[str, dict] = {}  # deduplicate by EAN
    offer_ean_map: dict[str, str] = {}  # offer_id → EAN (for canonical_product_id lookup)
    skipped_availability = 0
    skipped_same_price = 0
    compound_count = 0
    compound_products = 0

    def _add_mapped(offer_row: dict | None, product_row: dict | None) -> None:
        """Append a mapped offer/product pair, tracking skips."""
        if offer_row is None:
            return
        offer_rows.append(offer_row)
        if product_row:
            offer_ean_map[offer_row["id"]] = product_row["ean"]
            if product_row["ean"] not in product_rows_map:
                product_rows_map[product_row["ean"]] = product_row

    # ---- First pass: process regular offers, collect compound offer IDs ----
    compound_offer_ids: list[str] = []

    for raw_offer in offers_raw:
        try:
            if _is_compound_offer(raw_offer):
                compound_count += 1
                offer_id = raw_offer.get("id", "?")
                if offer_id != "?":
                    compound_offer_ids.append(offer_id)
                continue

            # ---- Regular single-product offer ----
            offer_row, product_row = map_offer(store_id, raw_offer)
            if offer_row is None:
                # Determine reason for skip (for logging)
                p = (raw_offer.get("pricing", {}) or {}).get("price")
                np = (raw_offer.get("normalPricing", {}) or {}).get("price")
                if p is not None and np is not None and p >= np:
                    skipped_same_price += 1
                else:
                    skipped_availability += 1
                continue
            _add_mapped(offer_row, product_row)
        except Exception:
            logger.warning(
                "Store %s: failed to map offer %s, skipping",
                store_id,
                raw_offer.get("id", "?"),
                exc_info=True,
            )

    # ---- Batch-fetch compound offers (multiple IDs per API call) ----
    if compound_offer_ids:
        logger.info(
            "Store %s: batch-fetching %d compound offers in %d call(s)",
            store_id,
            len(compound_offer_ids),
            (len(compound_offer_ids) + COMPOUND_FETCH_BATCH - 1) // COMPOUND_FETCH_BATCH,
        )
        for batch_ids in _chunked(compound_offer_ids, COMPOUND_FETCH_BATCH):
            try:
                detail = fetch_offers(store_id, batch_ids)
                detail_offers = detail.get("offers", [])

                # Build a lookup so we can match returned offers to IDs
                for detail_offer in detail_offers:
                    detail_id = detail_offer.get("id")
                    products_list = detail_offer.get("products", [])
                    if not products_list:
                        logger.debug(
                            "Store %s: compound offer %s has no products",
                            store_id, detail_id,
                        )
                        continue
                    for pw in products_list:
                        o_row, p_row = map_compound_product(
                            store_id, detail_offer, pw,
                        )
                        if o_row is None:
                            skipped_availability += 1
                            continue
                        _add_mapped(o_row, p_row)
                        compound_products += 1
            except Exception:
                logger.warning(
                    "Store %s: failed to batch-fetch compound offers %s, skipping",
                    store_id, batch_ids, exc_info=True,
                )

    if skipped_availability or skipped_same_price or compound_count:
        logger.info(
            "Store %s: skipped %d (availability) + %d (same-price), "
            "expanded %d compound offers → %d products",
            store_id, skipped_availability, skipped_same_price,
            compound_count, compound_products,
        )

    # 3. Upsert products
    product_rows = list(product_rows_map.values())
    if product_rows:
        _upsert_products(supabase, product_rows)
        logger.info("Store %s: upserted %d products", store_id, len(product_rows))

    # 4. Fetch product UUIDs and attach to offers
    eans = list(product_rows_map.keys())
    ean_to_id = _fetch_product_ids(supabase, eans)
    for row in offer_rows:
        ean = offer_ean_map.get(row["id"])
        if ean and ean in ean_to_id:
            row["canonical_product_id"] = ean_to_id[ean]
        else:
            row["canonical_product_id"] = None

    # 5. Upsert offers
    _upsert_offers(supabase, offer_rows)
    logger.info("Store %s: upserted %d offers", store_id, len(offer_rows))

    # 6. Delete stale offers
    deleted = _delete_stale_offers(supabase, f"k-ruoka:{store_id}", sync_time)
    if deleted:
        logger.info("Store %s: deleted %d stale offers", store_id, deleted)

    return len(offer_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point — sync Helsinki-area K-Ruoka stores and offers to Supabase."""

    # ---- validate env ----
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    supabase = create_client(supabase_url, supabase_key)
    logger.info("Supabase client initialised (%s)", supabase_url)

    t_start = time.perf_counter()
    sync_time = _now_iso()

    # ---- 1. Fetch stores ----
    logger.info("Fetching Helsinki-area K-Ruoka stores…")
    stores = fetch_helsinki_stores()
    logger.info("Found %d stores", len(stores))

    if not stores:
        logger.warning("No stores found — exiting")
        sys.exit(0)

    # ---- 2. Upsert stores ----
    upsert_stores(supabase, stores)

    # ---- 3. Sync offers for each store ----
    total_offers = 0
    errors: list[str] = []

    for idx, store in enumerate(stores, 1):
        sid = store["id"]
        logger.info(
            "--- [%d/%d] Syncing store %s (%s) ---",
            idx,
            len(stores),
            sid,
            store.get("name", ""),
        )
        try:
            count = sync_store_offers(supabase, sid, sync_time)
            total_offers += count
        except Exception:
            logger.error("Store %s FAILED", sid, exc_info=True)
            errors.append(sid)



    # ---- 4. Summary ----
    elapsed = time.perf_counter() - t_start
    logger.info("=" * 60)
    logger.info("Sync complete")
    logger.info("  Stores synced : %d", len(stores))
    logger.info("  Total offers  : %d", total_offers)
    logger.info("  Errors        : %d  %s", len(errors), errors if errors else "")
    logger.info("  Elapsed       : %.1f s (%.1f min)", elapsed, elapsed / 60)
    logger.info("=" * 60)

    # Fail the GH Actions job if too many stores errored out (> 25%)
    if errors and len(errors) > len(stores) * 0.25:
        logger.error(
            "Too many errors (%d/%d stores failed) — exiting with code 1",
            len(errors),
            len(stores),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
