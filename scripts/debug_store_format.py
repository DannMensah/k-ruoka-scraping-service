"""Debug script: print first store structure from K-Ruoka API."""
import json
import logging
import os
import sys
import atexit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from helpers import search_stores, close_browser

atexit.register(close_browser)

# Fetch a few stores
resp = search_stores(query="", limit=5)

print("\n=== RAW RESPONSE TYPE ===")
print(type(resp))

if isinstance(resp, list):
    stores = resp
    print(f"Response is a list with {len(stores)} items")
elif isinstance(resp, dict):
    print("Response keys:", list(resp.keys()))
    stores = resp.get("results", resp.get("stores", []))
else:
    print("Unexpected type:", type(resp))
    stores = []

if stores:
    print(f"\n=== FIRST STORE (all keys) ===")
    print(json.dumps(stores[0], indent=2, ensure_ascii=False)[:2000])

    # Look specifically for geo/location fields
    s = stores[0]
    print(f"\n=== GEO FIELDS ===")
    for k in s.keys():
        val = s[k]
        k_lower = k.lower()
        if any(x in k_lower for x in ["geo", "lat", "lon", "loc", "address", "coord", "pos"]):
            print(f"  {k}: {val}")
        elif isinstance(val, dict):
            for kk in val.keys():
                kk_lower = kk.lower()
                if any(x in kk_lower for x in ["geo", "lat", "lon", "loc", "coord", "pos"]):
                    print(f"  {k}.{kk}: {val[kk]}")
else:
    print("No stores found!")
