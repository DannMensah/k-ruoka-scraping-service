#!/usr/bin/env python3
"""Debug script: check for null prices in K-Ruoka offers for 1-2 stores."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import search_all_offers_for_store, fetch_offers
from sync_to_supabase import map_offer, map_compound_product, _is_compound_offer, _extract_product_fields, COMPOUND_FETCH_BATCH

def _chunked(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

store_ids = sys.argv[1:] or ["N110"]

for store_id in store_ids:
    print(f"\n{'='*60}")
    print(f"Store: {store_id}")
    print(f"{'='*60}")

    result = search_all_offers_for_store(store_id)
    offers_raw = result.get("offers", [])
    print(f"Total raw offers: {len(offers_raw)}")

    null_price_in_raw = 0
    compound_ids = []
    regular_null_price = 0
    regular_mapped_null = 0
    total_regular = 0

    for raw in offers_raw:
        pricing = raw.get("pricing", {}) or {}
        price = pricing.get("price")
        if price is None:
            null_price_in_raw += 1

        if _is_compound_offer(raw):
            oid = raw.get("id", "?")
            if oid != "?":
                compound_ids.append(oid)
            # Show the compound offer's pricing
            if price is None:
                print(f"  COMPOUND null-price: id={oid}, pricing={json.dumps(pricing)[:200]}")
        else:
            total_regular += 1
            # Try mapping
            offer_row, product_row = map_offer(store_id, raw)
            if offer_row is not None and offer_row.get("price") is None:
                regular_mapped_null += 1
                # Show details
                prod = (raw.get("product", {}) or {}).get("product", {}) or {}
                ms = (prod.get("mobilescan", {}) or {}).get("pricing", {}) or {}
                disc = ms.get("discount", {}) or {}
                batch = ms.get("batch", {}) or {}
                normal = ms.get("normal", {}) or {}
                print(f"  REGULAR null-price: id={raw.get('id')}")
                print(f"    top-level pricing.price = {price}")
                print(f"    discount.price = {disc.get('price')}")
                print(f"    batch.price = {batch.get('price')}")
                print(f"    normal.price = {normal.get('price')}")

    print(f"\nRaw null prices: {null_price_in_raw}/{len(offers_raw)}")
    print(f"Regular offers: {total_regular}, mapped with null price: {regular_mapped_null}")
    print(f"Compound offer IDs: {len(compound_ids)}")

    # Now check compound offers
    if compound_ids:
        compound_null_price = 0
        compound_total_products = 0
        for batch_ids in _chunked(compound_ids[:50], COMPOUND_FETCH_BATCH):  # Limit to 50
            detail = fetch_offers(store_id, batch_ids)
            for detail_offer in detail.get("offers", []):
                detail_pricing = detail_offer.get("pricing", {}) or {}
                detail_price = detail_pricing.get("price")
                products_list = detail_offer.get("products", [])
                for pw in products_list:
                    compound_total_products += 1
                    o_row, p_row = map_compound_product(store_id, detail_offer, pw)
                    if o_row is not None and o_row.get("price") is None:
                        compound_null_price += 1
                        prod = pw.get("product", {}) or {}
                        ms = (prod.get("mobilescan", {}) or {}).get("pricing", {}) or {}
                        disc = ms.get("discount", {}) or {}
                        batch = ms.get("batch", {}) or {}
                        print(f"  COMPOUND PRODUCT null-price: offer={detail_offer.get('id')}")
                        print(f"    top-level pricing.price = {detail_price}")
                        print(f"    discount.price = {disc.get('price')}")
                        print(f"    batch.price = {batch.get('price')}")

        print(f"Compound products checked: {compound_total_products}, null price: {compound_null_price}")
