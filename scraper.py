import os
import logging
from flask import Flask, jsonify, request
from helpers import (
    fetch_offer_categories,
    fetch_offer_category,
    fetch_offers,
    fetch_related_products,
    search_stores,
    search_product,
    search_offers,
    fetch_all_stores,
    fetch_all_categories,
    fetch_all_offers_for_category,
    fetch_all_offers_for_store,
    search_all_offers_for_store,
    validate_api_headers,
)

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)


@app.route("/offer-categories", methods=["POST"])
def offer_categories_endpoint():
    data = request.get_json()
    return jsonify(fetch_offer_categories(
        store_id=data.get("storeId"),
    ))


@app.route("/offer-category", methods=["POST"])
def offer_category_endpoint():
    data = request.get_json()
    return jsonify(fetch_offer_category(
        store_id=data.get("storeId"),
        category=data.get("category"),
        offset=data.get("offset", 0),
        limit=data.get("limit", 25),
        pricing=data.get("pricing", {}),
    ))


@app.route("/fetch-offers", methods=["POST"])
def fetch_offers_endpoint():
    data = request.get_json()
    return jsonify(fetch_offers(
        store_id=data.get("storeId"),
        offer_ids=data.get("offerIds"),
        pricing=data.get("pricing", {}),
    ))


@app.route("/related-products", methods=["POST"])
def related_products_endpoint():
    data = request.get_json()
    return jsonify(fetch_related_products(
        product_id=data.get("productId"),
        store_id=data.get("storeId"),
        segment_id=data.get("segmentId", 1565),
    ))


@app.route("/stores-search", methods=["POST"])
def stores_search_endpoint():
    data = request.get_json()
    return jsonify(search_stores(
        query=data.get("query", ""),
        offset=data.get("offset", 0),
        limit=data.get("limit", 2000),
    ))


@app.route("/product-search", methods=["POST"])
def product_search_endpoint():
    data = request.get_json()
    return jsonify(search_product(
        query=data.get("query"),
        store_id=data.get("storeId"),
        language=data.get("language", "fi"),
        offset=data.get("offset", 0),
        limit=data.get("limit", 100),
        discount_filter=data.get("discountFilter", False),
        is_tos_tr_offer=data.get("isTosTrOffer", False),
    ))


@app.route("/search-offers", methods=["POST"])
def search_offers_endpoint():
    """Search offers by category path using the GET search-offers API.

    Request JSON:
        storeId: str (required)
        categoryPath: str (optional, empty = all offers)
        offset: int (default 0)
        language: str (default "fi")

    Response: {totalHits, storeId, results, categoryName, suggestions}
    """
    data = request.get_json()
    return jsonify(search_offers(
        store_id=data.get("storeId"),
        category_path=data.get("categoryPath", ""),
        offset=data.get("offset", 0),
        language=data.get("language", "fi"),
    ))


# ---------------------------------------------------------------------------
# Bulk endpoints — designed for cron-job callers that sync to a database
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health_endpoint():
    """Validate that the K-Ruoka API headers still work.

    Returns 200 with {"ok": true} when all checks pass,
    or 503 with error details when something is broken.
    """
    result = validate_api_headers()
    status = 200 if result["ok"] else 503
    return jsonify(result), status


@app.route("/bulk/stores", methods=["GET"])
def bulk_stores_endpoint():
    """Return all K-Ruoka stores.

    Response: {"stores": [...], "count": int}
    """
    stores = fetch_all_stores()
    return jsonify({"stores": stores, "count": len(stores)})


@app.route("/bulk/store-categories", methods=["POST"])
def bulk_store_categories_endpoint():
    """Return all offer categories for a store.

    Request JSON: {"storeId": "N110"}
    Response: {"storeId": "...", "categories": [...], "count": int}
    """
    data = request.get_json()
    store_id = data.get("storeId")
    if not store_id:
        return jsonify({"error": "storeId is required"}), 400
    categories = fetch_all_categories(store_id)
    return jsonify({
        "storeId": store_id,
        "categories": categories,
        "count": len(categories),
    })


@app.route("/bulk/category-offers", methods=["POST"])
def bulk_category_offers_endpoint():
    """Return all offers for a single category in a store (handles pagination).

    Request JSON: {"storeId": "N110", "categorySlug": "juomat"}
    Response: {"storeId": "...", "category": "...", "totalHits": int,
               "offers": [...], "apiCalls": int, "elapsedSeconds": float}
    """
    data = request.get_json()
    store_id = data.get("storeId")
    slug = data.get("categorySlug")
    if not store_id or not slug:
        return jsonify({"error": "storeId and categorySlug are required"}), 400

    result = fetch_all_offers_for_category(store_id, slug)
    return jsonify({"storeId": store_id, **result})


@app.route("/bulk/store-offers", methods=["POST"])
def bulk_store_offers_endpoint():
    """Return ALL offers for a store using search-offers endpoint.

    Uses the more efficient search-offers API with empty category path
    to get all offers in a flat list. ~27 API calls per store vs ~60 with
    the category-by-category approach.

    Request JSON: {"storeId": "N110"}
    Response: {
        "storeId": "...",
        "totalHits": int,
        "offers": [...],
        "apiCalls": int,
        "elapsedSeconds": float
    }
    """
    data = request.get_json()
    store_id = data.get("storeId")
    if not store_id:
        return jsonify({"error": "storeId is required"}), 400

    result = search_all_offers_for_store(store_id)
    return jsonify(result)


@app.route("/bulk/store-offers-by-category", methods=["POST"])
def bulk_store_offers_by_category_endpoint():
    """Return ALL offers for a store grouped by category (old approach).

    Uses the category-by-category pagination. Slower but provides
    category-level breakdown.

    Request JSON: {"storeId": "N110"}
    Response: {
        "storeId": "...",
        "categories": [...],
        "totalOffers": int,
        "totalApiCalls": int,
        "totalElapsedSeconds": float
    }
    """
    data = request.get_json()
    store_id = data.get("storeId")
    if not store_id:
        return jsonify({"error": "storeId is required"}), 400

    result = fetch_all_offers_for_store(store_id)
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)