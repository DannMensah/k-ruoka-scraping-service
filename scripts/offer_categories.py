"""
Fetch available offer categories for a store.

Usage: python scripts/offer_categories.py [store_id]
"""
import requests, json, sys

BASE = "http://localhost:5000"
store_id = sys.argv[1] if len(sys.argv) > 1 else "N110"

r = requests.post(f"{BASE}/offer-categories", json={"storeId": store_id})
if r.status_code != 200:
    print(f"Error: {r.status_code}")
    print(r.text)
else:
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))
