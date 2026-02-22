"""
Tests for the K-Ruoka scraping service.

Covers:
  - API connectivity validation (catches stale build numbers / experiments)
  - Individual helper functions
  - Bulk helper functions (search-offers and category-based)
  - Flask endpoint integration tests
  - Response-shape contracts so breakages are caught early

Run:
    python -m pytest tests/ -v

Note: These tests require a running Chrome instance (DrissionPage).
On the first run, you may need to solve a Cloudflare challenge in the
Chrome window that opens.
"""
import time
import pytest
from helpers import (
    BASE_URL,
    API_HEADERS,
    MAX_OFFER_CATEGORY_LIMIT,
    SEARCH_OFFERS_PAGE_SIZE,
    _post_raw,
    search_stores,
    search_offers,
    fetch_offer_categories,
    fetch_offer_category,
    fetch_all_stores,
    fetch_all_categories,
    fetch_all_offers_for_category,
    fetch_all_offers_for_store,
    search_all_offers_for_store,
    validate_api_headers,
)
from scraper import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(scope="module")
def sample_store_id():
    """A known-good store ID. N110 = K-Supermarket Helsinki."""
    return "N110"


# Slow-test marker — these actually hit the K-Ruoka API.
# Run with:  pytest tests/ -v -m "not slow"  to skip them.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# 1. Header / connectivity validation
# ---------------------------------------------------------------------------

class TestHeaders:
    """Ensure the K-Ruoka API is reachable via the browser transport."""

    def test_stores_search_returns_200(self):
        resp = _post_raw("stores/search", {"query": "", "offset": 0, "limit": 1})
        assert resp.status_code == 200, (
            f"stores/search returned {resp.status_code} — "
            "browser transport or headers may be broken"
        )

    def test_offer_categories_returns_200(self, sample_store_id):
        time.sleep(0.5)
        resp = _post_raw("offer-categories", {"storeId": sample_store_id})
        assert resp.status_code == 200, (
            f"offer-categories returned {resp.status_code} — "
            "headers may be outdated"
        )

    def test_offer_category_returns_200(self, sample_store_id):
        time.sleep(0.5)
        resp = _post_raw("offer-category", {
            "storeId": sample_store_id,
            "category": {"kind": "productCategory", "slug": "juomat"},
            "offset": 0,
            "limit": 1,
            "pricing": {},
        })
        assert resp.status_code == 200, (
            f"offer-category returned {resp.status_code} — "
            "headers may be outdated"
        )

    def test_required_api_headers_present(self):
        """Verify all required API headers are configured."""
        required = ["x-k-build-number", "x-k-experiments"]
        for h in required:
            assert h in API_HEADERS, f"Missing required header: {h}"
            assert API_HEADERS[h], f"Empty required header: {h}"

    def test_offer_category_limit_25_accepted(self, sample_store_id):
        """Limit=25 should succeed (the max)."""
        time.sleep(0.5)
        resp = _post_raw("offer-category", {
            "storeId": sample_store_id,
            "category": {"kind": "productCategory", "slug": "juomat"},
            "offset": 0,
            "limit": MAX_OFFER_CATEGORY_LIMIT,
            "pricing": {},
        })
        assert resp.status_code == 200

    def test_offer_category_limit_26_rejected(self, sample_store_id):
        """Limit>25 should be rejected (400). If this passes with 200,
        the max limit has changed and we can increase page size."""
        time.sleep(0.5)
        resp = _post_raw("offer-category", {
            "storeId": sample_store_id,
            "category": {"kind": "productCategory", "slug": "juomat"},
            "offset": 0,
            "limit": MAX_OFFER_CATEGORY_LIMIT + 1,
            "pricing": {},
        })
        assert resp.status_code == 400, (
            "limit>25 returned 200 — the API max limit may have increased, "
            "update MAX_OFFER_CATEGORY_LIMIT in helpers.py"
        )

    def test_validate_api_headers_helper(self):
        """The validate_api_headers() helper should report all OK."""
        result = validate_api_headers()
        assert result["ok"] is True, f"Header validation failed: {result['errors']}"


# ---------------------------------------------------------------------------
# 2. Individual helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_search_stores_returns_list(self):
        time.sleep(0.5)
        stores = fetch_all_stores()
        assert isinstance(stores, list)
        assert len(stores) > 0, "No stores returned"

    def test_store_has_required_fields(self):
        time.sleep(0.5)
        stores = fetch_all_stores()
        store = stores[0]
        for field in ["id", "name", "chain"]:
            assert field in store, f"Store missing required field: {field}"

    def test_fetch_offer_categories_returns_list(self, sample_store_id):
        time.sleep(0.5)
        categories = fetch_all_categories(sample_store_id)
        assert isinstance(categories, list)
        assert len(categories) > 0

    def test_category_has_required_fields(self, sample_store_id):
        time.sleep(0.5)
        categories = fetch_all_categories(sample_store_id)
        cat = categories[0]
        for field in ["slug", "count", "name"]:
            assert field in cat, f"Category missing required field: {field}"
        assert isinstance(cat["name"], dict)
        assert "finnish" in cat["name"]

    def test_fetch_offer_category_returns_offers(self, sample_store_id):
        time.sleep(0.5)
        resp = fetch_offer_category(
            store_id=sample_store_id,
            category={"kind": "productCategory", "slug": "juomat"},
            offset=0,
            limit=5,
        )
        assert "offers" in resp
        assert "totalHits" in resp
        assert isinstance(resp["offers"], list)
        assert resp["totalHits"] > 0

    def test_offer_has_required_fields(self, sample_store_id):
        time.sleep(0.5)
        resp = fetch_offer_category(
            store_id=sample_store_id,
            category={"kind": "productCategory", "slug": "juomat"},
            offset=0,
            limit=1,
        )
        offer = resp["offers"][0]
        for field in ["id", "pricing", "offerType"]:
            assert field in offer, f"Offer missing required field: {field}"

    def test_search_offers_returns_results(self, sample_store_id):
        """search_offers with a category returns matching offers."""
        time.sleep(0.5)
        resp = search_offers(
            store_id=sample_store_id,
            category_path="juomat",
            offset=0,
        )
        assert "totalHits" in resp
        assert "results" in resp
        assert resp["totalHits"] > 0
        assert isinstance(resp["results"], list)
        assert len(resp["results"]) > 0

    def test_search_offers_empty_category_returns_all(self, sample_store_id):
        """search_offers with empty category returns all offers for the store."""
        time.sleep(0.5)
        resp = search_offers(
            store_id=sample_store_id,
            category_path="",
            offset=0,
        )
        assert "totalHits" in resp
        assert resp["totalHits"] > 0
        assert len(resp["results"]) > 0

    def test_search_offers_page_size(self, sample_store_id):
        """search_offers returns at most SEARCH_OFFERS_PAGE_SIZE results."""
        time.sleep(0.5)
        resp = search_offers(
            store_id=sample_store_id,
            category_path="",
            offset=0,
        )
        assert len(resp["results"]) <= SEARCH_OFFERS_PAGE_SIZE


# ---------------------------------------------------------------------------
# 3. Bulk helper functions
# ---------------------------------------------------------------------------

class TestBulkHelpers:
    def test_fetch_all_offers_for_category(self, sample_store_id):
        time.sleep(0.5)
        result = fetch_all_offers_for_category(sample_store_id, "juomat")
        assert result["category"] == "juomat"
        assert result["totalHits"] > 0
        assert len(result["offers"]) == result["totalHits"]
        assert result["apiCalls"] >= 1
        assert result["elapsedSeconds"] > 0

    def test_fetch_all_offers_for_small_category(self, sample_store_id):
        """A category with <=25 offers should require exactly 1 API call."""
        time.sleep(0.5)
        cats = fetch_all_categories(sample_store_id)
        time.sleep(0.5)
        small_cat = next(
            (c for c in cats if 0 < c.get("count", 0) <= MAX_OFFER_CATEGORY_LIMIT),
            None,
        )
        if small_cat is None:
            pytest.skip("No small category found")
        result = fetch_all_offers_for_category(sample_store_id, small_cat["slug"])
        assert result["apiCalls"] == 1

    def test_fetch_all_offers_for_store_structure(self, sample_store_id):
        """fetch_all_offers_for_store returns the correct shape (category-based)."""
        time.sleep(0.5)
        result = fetch_all_offers_for_store(sample_store_id)
        assert result["storeId"] == sample_store_id
        assert isinstance(result["categories"], list)
        assert result["totalOffers"] > 0
        assert result["totalApiCalls"] > 0
        assert result["totalElapsedSeconds"] > 0
        cat = result["categories"][0]
        assert "category" in cat
        assert "offers" in cat
        assert isinstance(cat["offers"], list)

    def test_search_all_offers_for_store_structure(self, sample_store_id):
        """search_all_offers_for_store returns a flat list of all offers."""
        time.sleep(0.5)
        result = search_all_offers_for_store(sample_store_id)
        assert result["storeId"] == sample_store_id
        assert result["totalHits"] > 0
        assert isinstance(result["offers"], list)
        assert len(result["offers"]) == result["totalHits"]
        assert result["apiCalls"] >= 1
        assert result["elapsedSeconds"] > 0

    def test_search_all_offers_fewer_api_calls(self, sample_store_id):
        """search_all_offers should use fewer API calls than category-based."""
        time.sleep(0.5)
        search_result = search_all_offers_for_store(sample_store_id)
        assert search_result["apiCalls"] < 40, (
            f"Expected fewer than 40 API calls, got {search_result['apiCalls']}"
        )


# ---------------------------------------------------------------------------
# 4. Flask endpoint integration tests
# ---------------------------------------------------------------------------

class TestEndpoints:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_bulk_stores(self, client):
        time.sleep(0.5)
        resp = client.get("/bulk/stores")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "stores" in data
        assert "count" in data
        assert data["count"] > 0
        assert len(data["stores"]) == data["count"]

    def test_bulk_store_categories(self, client, sample_store_id):
        time.sleep(0.5)
        resp = client.post("/bulk/store-categories", json={"storeId": sample_store_id})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["storeId"] == sample_store_id
        assert data["count"] > 0
        assert len(data["categories"]) == data["count"]

    def test_bulk_store_categories_missing_store_id(self, client):
        resp = client.post("/bulk/store-categories", json={})
        assert resp.status_code == 400

    def test_bulk_category_offers(self, client, sample_store_id):
        time.sleep(0.5)
        resp = client.post("/bulk/category-offers", json={
            "storeId": sample_store_id,
            "categorySlug": "juomat",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["storeId"] == sample_store_id
        assert data["category"] == "juomat"
        assert data["totalHits"] > 0
        assert len(data["offers"]) == data["totalHits"]

    def test_bulk_category_offers_missing_params(self, client):
        resp = client.post("/bulk/category-offers", json={"storeId": "N110"})
        assert resp.status_code == 400

    def test_bulk_store_offers(self, client, sample_store_id):
        """POST /bulk/store-offers returns flat list via search-offers."""
        time.sleep(0.5)
        resp = client.post("/bulk/store-offers", json={"storeId": sample_store_id})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["storeId"] == sample_store_id
        assert data["totalHits"] > 0
        assert isinstance(data["offers"], list)
        assert len(data["offers"]) == data["totalHits"]
        assert data["apiCalls"] >= 1
        assert data["elapsedSeconds"] > 0

    def test_bulk_store_offers_missing_store_id(self, client):
        resp = client.post("/bulk/store-offers", json={})
        assert resp.status_code == 400

    def test_bulk_store_offers_by_category(self, client, sample_store_id):
        """POST /bulk/store-offers-by-category returns grouped results."""
        time.sleep(0.5)
        resp = client.post("/bulk/store-offers-by-category", json={"storeId": sample_store_id})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["storeId"] == sample_store_id
        assert data["totalOffers"] > 0
        assert data["totalApiCalls"] > 0
        assert isinstance(data["categories"], list)
        assert len(data["categories"]) > 0

    def test_search_offers_endpoint(self, client, sample_store_id):
        """POST /search-offers returns search results."""
        time.sleep(0.5)
        resp = client.post("/search-offers", json={
            "storeId": sample_store_id,
            "categoryPath": "juomat",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "totalHits" in data
        assert "results" in data

    # Existing endpoints still work
    def test_stores_search_endpoint(self, client):
        time.sleep(0.5)
        resp = client.post("/stores-search", json={"query": "", "limit": 2})
        assert resp.status_code == 200

    def test_offer_categories_endpoint(self, client, sample_store_id):
        time.sleep(0.5)
        resp = client.post("/offer-categories", json={"storeId": sample_store_id})
        assert resp.status_code == 200
