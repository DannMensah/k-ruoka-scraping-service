"""
Search for K-Ruoka store locations.

Usage: python scripts/stores_search.py [query] [limit]
"""
import requests, json, sys

BASE = "http://localhost:5000"
query = sys.argv[1] if len(sys.argv) > 1 else ""
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 2000

r = requests.post(f"{BASE}/stores-search", json={
    "query": query,
    "offset": 0,
    "limit": limit
})
if r.status_code != 200:
    print(f"Error: {r.status_code}")
    print(r.text)
else:
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))
