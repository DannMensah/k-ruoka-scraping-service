#!/usr/bin/env python3
"""
Search offers by category path.

Usage:
    python search_offers.py [store_id] [category_path] [offset] [language]

Examples:
    python search_offers.py                                  # default N110, liha-ja-kasviproteiinit, offset 0
    python search_offers.py N110 juomat 0                   # search juomat (drinks)
    python search_offers.py N110 liha-ja-kasviproteiinit 50 # pagination
"""
import sys
import json
import os

# Add parent directory to path so we can import helpers
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import search_offers


def main():
    store_id = sys.argv[1] if len(sys.argv) > 1 else "N110"
    category_path = sys.argv[2] if len(sys.argv) > 2 else "liha-ja-kasviproteiinit"
    offset = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    language = sys.argv[4] if len(sys.argv) > 4 else "fi"

    print(f"Searching offers: storeId={store_id}, categoryPath={category_path}, offset={offset}, language={language}")
    print()

    result = search_offers(
        store_id=store_id,
        category_path=category_path,
        offset=offset,
        language=language,
    )

    offers = result.get("results", [])
    print(f"Category: {result.get('categoryName')}")
    print(f"Total hits: {result.get('totalHits')}")
    print(f"Returned: {len(offers)} offers (offset={offset})")
    print()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
