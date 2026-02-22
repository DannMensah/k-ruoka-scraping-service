"""
Fetch related products for a specific product.

Usage: python scripts/related_products.py [product_id] [store_id] [segment_id]
"""
import requests, json, sys

BASE = "http://localhost:5000"
product_id = sys.argv[1] if len(sys.argv) > 1 else "6410405078872"
store_id = sys.argv[2] if len(sys.argv) > 2 else "N110"
segment_id = int(sys.argv[3]) if len(sys.argv) > 3 else 1565

r = requests.post(f"{BASE}/related-products", json={
    "productId": product_id,
    "storeId": store_id,
    "segmentId": segment_id
})
if r.status_code != 200:
    print(f"Error: {r.status_code}")
    print(r.text)
else:
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))
