"""
Fetch product offers for a specific category.

Usage: python scripts/offer_category.py [store_id] [category_slug] [limit] [offset]
"""
import requests, json, sys

BASE = "http://localhost:5000"
store_id = sys.argv[1] if len(sys.argv) > 1 else "N110"
slug = sys.argv[2] if len(sys.argv) > 2 else "hedelmat-ja-vihannekset"
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
offset = int(sys.argv[4]) if len(sys.argv) > 4 else 0

r = requests.post(f"{BASE}/offer-category", json={
    "storeId": store_id,
    "category": {"kind": "productCategory", "slug": slug},
    "offset": offset,
    "limit": limit,
    "pricing": {}
})
if r.status_code != 200:
    print(f"Error: {r.status_code}")
    print(r.text)
else:
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))
