"""
Fetch details for specific offers by ID.

Usage: python scripts/fetch_offers.py [store_id] [offer_id1] [offer_id2] ...
"""
import requests, json, sys

BASE = "http://localhost:5000"
store_id = sys.argv[1] if len(sys.argv) > 1 else "N110"
offer_ids = sys.argv[2:] if len(sys.argv) > 2 else ["301851P"]

r = requests.post(f"{BASE}/fetch-offers", json={
    "storeId": store_id,
    "offerIds": offer_ids,
    "pricing": {}
})
if r.status_code != 200:
    print(f"Error: {r.status_code}")
    print(r.text)
else:
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))
