"""
Fetch all stores, then all offers for a specific store.
Usage: python scripts/bulk_store_offers.py [store_id]
"""
import requests, json, sys, time

BASE = "http://localhost:5000"
store_id = sys.argv[1] if len(sys.argv) > 1 else "N110"

print(f"Fetching all offers for store {store_id}...")
t0 = time.perf_counter()
r = requests.post(f"{BASE}/bulk/store-offers", json={"storeId": store_id})
elapsed = time.perf_counter() - t0

if r.status_code != 200:
    print(f"Error: {r.status_code}")
    print(r.text)
else:
    data = r.json()
    print(f"Done in {elapsed:.1f}s")
    print(f"  Categories: {len(data['categories'])}")
    print(f"  Total offers: {data['totalOffers']}")
    print(f"  API calls: {data['totalApiCalls']}")
    print(f"  Server time: {data['totalElapsedSeconds']}s")
    print()
    for cat in data["categories"]:
        print(f"  {cat['category']}: {len(cat['offers'])} offers")
