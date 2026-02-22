"""
Fetch all K-Ruoka stores.
Usage: python scripts/bulk_stores.py
"""
import requests, json, sys

BASE = "http://localhost:5000"

r = requests.get(f"{BASE}/bulk/stores")
if r.status_code != 200:
    print(f"Error: {r.status_code}")
    print(r.text)
else:
    data = r.json()
    print(f"Total stores: {data['count']}")
    # Print first few
    for store in data["stores"][:10]:
        print(f"  {store['id']} — {store['name']} ({store.get('chain', '?')})")
    if data["count"] > 10:
        print(f"  ... and {data['count'] - 10} more")
