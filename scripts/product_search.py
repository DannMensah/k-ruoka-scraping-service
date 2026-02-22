import requests
import sys
import json

BASE_URL = "http://localhost:5000"

def product_search(query, store_id="N110", language="fi", offset=0, limit=100, discount_filter=False, is_tos_tr_offer=False):
    payload = {
        "query": query,
        "storeId": store_id,
        "language": language,
        "offset": offset,
        "limit": limit,
        "discountFilter": discount_filter,
        "isTosTrOffer": is_tos_tr_offer,
    }
    response = requests.post(f"{BASE_URL}/product-search", json=payload)
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    # Default search query or from command line
    query = sys.argv[1] if len(sys.argv) > 1 else "maito"
    
    # Optional parameters from command line
    store_id = sys.argv[2] if len(sys.argv) > 2 else "N110"
    language = sys.argv[3] if len(sys.argv) > 3 else "fi"
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 100
    
    print(f"Searching for '{query}' in store {store_id}...")
    result = product_search(query, store_id, language, limit=limit)
    print(json.dumps(result, indent=2))
