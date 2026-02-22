# K-Ruoka Scraping Service

A Flask-based HTTP service that scrapes product offers, categories, and store information from K-Ruoka (Kesko, Finland's largest grocery retailer). Intended as a data provider for a downstream service that stores offers in a database.

## Base URL

Local: `http://localhost:5000`  
Deployed: see `render.yaml` / Render dashboard.

---

## Sync Strategy (recommended for Vercel 5-min cron jobs)

The K-Ruoka API has ~1,060 stores. Syncing all stores sequentially in one call would take ~6+ hours. The recommended approach:

| Job            | Call                      | What it does                             |
| -------------- | ------------------------- | ---------------------------------------- |
| Bootstrap      | `GET /health`             | Verify headers are valid before starting |
| Bootstrap      | `GET /bulk/stores`        | Fetch & persist all 1,060 stores         |
| Per-store cron | `POST /bulk/store-offers` | Fetch all offers for one store (~55s)    |

Fan out the per-store jobs — each one completes well within a 5-minute window.

---

## Endpoints

### `GET /health`

Validates that the K-Ruoka API headers are still accepted. Call this before any sync run. Returns 503 if headers are stale (the `x-k-build-number` or `x-k-experiments` values in `helpers.py` need updating).

```json
{
  "ok": true,
  "storesStatus": 200,
  "categoriesStatus": 200,
  "offersStatus": 200,
  "errors": []
}
```

---

### `GET /bulk/stores`

Returns all K-Ruoka stores. Use this to build/refresh the stores table.

```json
{
  "count": 1060,
  "stores": [ ... ]
}
```

Each store object:

```json
{
  "id": "N110",
  "name": "K-Supermarket Kamppi",
  "shortName": "Kamppi",
  "shortestName": "Kamppi",
  "slug": "k-supermarket-kamppi",
  "chain": "ksupermarket",
  "chainAbbreviation": "KSM",
  "chainName": "K-Supermarket",
  "branchCode": "N110",
  "isWebStore": true,
  "hasPickup": true,
  "hasPickupBox": false,
  "hasHomeDelivery": true,
  "hasExpressDelivery": false,
  "expressDeliveryProvider": null,
  "location": {
    "address": "Urho Kekkosen katu 1",
    "city": "Helsinki",
    "postalCode": "00100",
    "country": "fi"
  },
  "geo": {
    "lat": 60.169,
    "lon": 24.934
  },
  "legacySlugs": [],
  "deliveryMethods": ["homeDelivery", "pickup"],
  "retailerImage": "https://..."
}
```

Key fields to persist: `id` (primary key), `name`, `chain`, `chainName`, `location`, `geo`, `isWebStore`, delivery flags.

---

### `POST /bulk/store-categories`

Returns all offer categories for a single store.

**Request:**

```json
{ "storeId": "N110" }
```

**Response:**

```json
{
  "storeId": "N110",
  "count": 27,
  "categories": [ ... ]
}
```

Each category object:

```json
{
  "slug": "juomat",
  "count": 112,
  "name": {
    "finnish": "Juomat",
    "swedish": "Drycker",
    "english": "Drinks"
  }
}
```

`slug` is the category identifier used in subsequent calls. `count` is the total number of offers in that category for this store.

---

### `POST /bulk/category-offers`

Returns all offers in one category for one store. Handles pagination internally (max 25 per page from K-Ruoka API).

**Request:**

```json
{
  "storeId": "N110",
  "categorySlug": "juomat"
}
```

**Response:**

```json
{
  "storeId": "N110",
  "category": "juomat",
  "totalHits": 112,
  "offers": [ ... ],
  "apiCalls": 5,
  "elapsedSeconds": 4.2
}
```

---

### `POST /bulk/store-offers` ← **main sync endpoint**

Returns ALL offers for a store across every category. This is the primary endpoint for syncing one store to a database. Takes ~55s per store.

**Request:**

```json
{ "storeId": "N110" }
```

**Response:**

```json
{
  "storeId": "N110",
  "totalOffers": 1265,
  "totalApiCalls": 65,
  "totalElapsedSeconds": 54.8,
  "categories": [
    {
      "category": "juomat",
      "totalHits": 112,
      "offers": [ ... ],
      "apiCalls": 5,
      "elapsedSeconds": 4.2
    }
  ]
}
```

---

## Offer Object

This is the core data structure returned in every `offers` array. Two shapes exist depending on whether K-Ruoka links a specific product to the offer.

### Offer with a single linked product (`/offer-category`, `/bulk/*`)

```json
{
  "id": "S4177155P",
  "campaignId": "11596034",
  "offerNameId": null,
  "offerType": "store",
  "title": "Suvi porkkanasose 1kg Suomi",
  "localizedTitle": {
    "english": "Suvi carrot puree 1kg Finland",
    "finnish": "Suvi porkkanasose 1kg Suomi",
    "swedish": "Suvi morotpuré 1kg Finland"
  },
  "image": "https://public.keskofiles.com/f/k-ruoka-offers/11596034/...",
  "pricing": {
    "price": 2.99,
    "unit": { "fi": "ps", "sv": "ps" },
    "discountType": "STANDARD",
    "discountAvailability": { "store": true, "web": true }
  },
  "normalPricing": {
    "price": 5.76,
    "unit": { "fi": "kpl", "sv": "st" }
  },
  "product": { ... }
}
```

### Offer with multiple linked products (`/fetch-offers`)

When fetched via `/fetch-offers`, an offer may contain a `products` array (multiple EANs in the same deal) instead of a single `product` field:

```json
{
  "id": "301851P",
  "campaignId": "200083711",
  "offerNameId": "200083711_00003",
  "offerType": "chain",
  "title": "Myrttisen mini perinteiset suola- ja maustekurkut 240 g",
  "localizedTitle": { ... },
  "image": "https://...",
  "pricing": { ... },
  "normalPricing": { ... },
  "products": [
    { "id": "6430025259949", "product": { ... }, "score": 0, "type": "product" },
    { "id": "6418388005168", "product": { ... }, "score": 0, "type": "product" }
  ]
}
```

### `offerType` values

| Value     | Meaning                                                      |
| --------- | ------------------------------------------------------------ |
| `"chain"` | National K-ruoka chain-wide offer (same price at all stores) |
| `"store"` | Store-specific offer (price set by the individual store)     |

### `offerNameId`

Present only on `"chain"` offers. Format: `"{campaignId}_{variant}"`, e.g. `"200083711_00001"`. Identifies a specific product variant within a multi-product campaign. `null` on store offers.

---

## Product Object

Nested inside each offer as `offer.product` (single) or `offer.products[n]` (multi). The actual product data lives in `product.product`:

```json
{
  "id": "6418248002382",
  "score": 0,
  "type": "product",
  "product": {
    "id": "6418248002382",
    "ean": "6418248002382",
    "baseEan": "6418248002382",
    "kind": "v3",
    "type": "product",
    "isAvailable": true,
    "isReferenceEan": false,
    "popularity": 7.2,
    "section": "1648",

    "localizedName": {
      "english": "...",
      "finnish": "...",
      "swedish": "..."
    },

    "images": [
      "https://public.keskofiles.com/f/k-ruoka/product/6418248002382",
      "https://public.keskofiles.com/f/k-ruoka/product/0NNS0/6418248002382"
    ],

    "store": { "id": "N110", "isLocal": false },

    "availability": { "store": true, "web": true },

    "adInfo": { "highlightAd": false, "isSponsored": false },

    "brand": {
      "id": "1",
      "name": "Pirkka",
      "slug": "pirkka-1"
    },

    "ingredientId": "1648300_328",

    "category": { ... },

    "productAttributes": { ... },

    "mobilescan": { ... }
  }
}
```

`brand` is absent for unbranded products (e.g. loose produce).

### `product.category`

Describes the product's position in the K-Ruoka category tree (up to 3 levels deep):

```json
{
  "localizedName": {
    "english": "Fruit and vegetables",
    "finnish": "Hedelmät ja vihannekset",
    "swedish": "Frukt och grönt"
  },
  "path": "hedelmat-ja-vihannekset/juurekset/jalostetut-juurekset",
  "order": 25,
  "tree": [
    {
      "slug": "hedelmat-ja-vihannekset",
      "localizedName": {
        "english": "Fruit and vegetables",
        "finnish": "...",
        "swedish": "..."
      }
    },
    {
      "slug": "hedelmat-ja-vihannekset/juurekset",
      "localizedName": {
        "english": "Potatoes and root vegetables",
        "finnish": "...",
        "swedish": "..."
      }
    },
    {
      "slug": "hedelmat-ja-vihannekset/juurekset/jalostetut-juurekset",
      "localizedName": {
        "english": "Prepared root vegetables",
        "finnish": "...",
        "swedish": "..."
      }
    }
  ]
}
```

Use `tree[0].slug` for the top-level category, `tree[-1].slug` for the most specific.

### `product.productAttributes`

Static product metadata from PIM (product information management):

```json
{
  "ean": "6418248002382",
  "pimId": "21870073",
  "section": "1648",
  "urlSlug": "suvi-porkkanasose-1kg-suomi-6418248002382",
  "labelName": { "fi": "Suvi porkkanasose 1kg Suomi", "sv": "..." },
  "marketingName": { "en": "...", "fi": "...", "sv": "..." },
  "image": {
    "url": "https://public.keskofiles.com/f/k-ruoka/product/6418248002382"
  },
  "measurements": {
    "contentSize": 1,
    "contentUnit": "kg",
    "netWeight": 1,
    "grossWeight": 1.01,
    "width": 32,
    "height": 4,
    "length": 13,
    "averageWeight": null
  },
  "origin": {
    "domestic": true,
    "countryOfOrigin": "fi",
    "countryOfOriginI18n": { "en": "Finland", "fi": "Suomi", "sv": "Finland" }
  },
  "meta": {
    "source": "tps",
    "isAlcohol": false,
    "isUtility": false,
    "alcoholStatus": "allowed",
    "deprecatedEan": false,
    "hiddenInRecipe": false,
    "isInternalCode": false,
    "canBeShownInWeb": true
  },
  "description": {}
}
```

`isInternalCode: true` means the EAN is a K-Ruoka internal (store-weighed) code rather than a standard barcode EAN.

---

## Pricing

All pricing is in **euros (€)**. Three pricing tiers exist inside `product.mobilescan.pricing`:

### Normal price

Always present:

```json
{
  "price": 5.76,
  "unit": "kpl",
  "localizedUnit": { "fi": "kpl", "sv": "st" },
  "isApproximate": false,
  "soldBy": { "kind": "piece" },
  "unitPrice": { "value": 5.76, "unit": "kg", "contentSize": 1 }
}
```

### Discount price (single-unit offer)

Present when the product has a `STANDARD` discount:

```json
{
  "price": 2.99,
  "unit": "kpl",
  "localizedUnit": { "fi": "kpl", "sv": "st" },
  "isApproximate": false,
  "soldBy": { "kind": "piece" },
  "discountType": "STANDARD",
  "discountPercentage": 48,
  "discountSource": "POS",
  "startDate": "2026-01-28T05:30:49.000Z",
  "endDate": "2026-02-15T21:59:59.000Z",
  "validNumberOfDaysLeft": 2,
  "campaignId": "11574370",
  "storeCampaignId": "11596034",
  "discountAvailability": { "store": true, "web": true },
  "unitPrice": { "value": 2.99, "unit": "kg", "contentSize": 1 }
}
```

`discountSource` is `"POS"` for store offers and `"SAP"` for chain offers.

### Batch price (multi-unit deal, e.g. "2 for €6")

Present when the deal requires buying multiple units:

```json
{
  "price": 6.0,
  "amount": 2,
  "unit": "kpl",
  "localizedUnit": { "fi": "kpl", "sv": "st" },
  "isApproximate": false,
  "soldBy": { "kind": "piece" },
  "discountType": "STANDARD",
  "discountPercentage": 39,
  "discountSource": "SAP",
  "startDate": "...",
  "endDate": "...",
  "validNumberOfDaysLeft": 2,
  "campaignId": "200083711",
  "discountAvailability": { "store": true, "web": true },
  "unitPrice": { "value": 12.5, "unit": "kg", "contentSize": 0.48 }
}
```

`amount` = number of units required to get the batch price.

### `soldBy.kind` values

| Value                | Meaning                                                                |
| -------------------- | ---------------------------------------------------------------------- |
| `"piece"`            | Fixed unit price (scan & pay per item)                                 |
| `"approximatePiece"` | Weighed at register; `soldBy.averageWeight` gives typical weight in kg |

For `approximatePiece` products, the shelf price (e.g. `0.99 €/kg`) is in `unitPrice.value` and the display price (`price: 0.17`) is estimated from `averageWeight`.

### Top-level offer pricing vs product pricing

| Location                                            | Description                                      |
| --------------------------------------------------- | ------------------------------------------------ |
| `offer.pricing`                                     | Discounted price as displayed on the shelf label |
| `offer.normalPricing`                               | Normal (non-discounted) price                    |
| `offer.product.product.mobilescan.pricing.normal`   | Canonical normal price (most reliable)           |
| `offer.product.product.mobilescan.pricing.discount` | Canonical discount price                         |
| `offer.product.product.mobilescan.pricing.batch`    | Canonical batch price                            |

**Recommendation:** Use `mobilescan.pricing` as the authoritative source for all prices. It includes structured data (dates, percentages, unit prices) absent from the top-level `pricing` field.

---

## Localization

All user-visible strings are available in Finnish, Swedish, and (usually) English. The language key naming is inconsistent across fields:

| Location                          | Keys used                                                |
| --------------------------------- | -------------------------------------------------------- |
| `offer.localizedTitle`            | `"english"`, `"finnish"`, `"swedish"`                    |
| `product.localizedName`           | `"english"`, `"finnish"`, `"swedish"`                    |
| `category.localizedName`          | `"english"`, `"finnish"`, `"swedish"`                    |
| `productAttributes.labelName`     | `"fi"`, `"sv"` (no English)                              |
| `productAttributes.marketingName` | `"en"`, `"fi"`, `"sv"`                                   |
| `pricing.unit`                    | `"fi"`, `"sv"` (e.g. `"kpl"` = piece, `"kg"` = kilogram) |

Always check for `null` / missing keys — English is sometimes absent on older products.

---

## IDs and Keys

| Field                                    | Type           | Description                                                                      |
| ---------------------------------------- | -------------- | -------------------------------------------------------------------------------- |
| `store.id` / `store.branchCode`          | String         | e.g. `"N110"` — primary key for stores                                           |
| `offer.id`                               | String         | e.g. `"S4177155P"` (store offer) or `"301851P"` (chain offer) — unique per offer |
| `offer.campaignId`                       | String         | Shared across all offers in the same campaign                                    |
| `offer.offerNameId`                      | String \| null | Variant identifier within a campaign; only on chain offers                       |
| `product.ean` / `product.id`             | String         | EAN-13 barcode or internal code; primary key for products                        |
| `product.baseEan`                        | String         | Canonical EAN (may differ from `ean` for variants)                               |
| `product.pimId` (in `productAttributes`) | String         | Kesko internal PIM identifier                                                    |
| `product.ingredientId`                   | String         | Ingredient/recipe system ID                                                      |
| `product.section`                        | String         | Kesko department code (e.g. `"1648"`)                                            |

---

## Suggested Database Schema

For a relational database:

```
stores          (id PK, name, chain, chain_name, city, address, postal_code, lat, lon, is_web_store, ...)
categories      (slug PK, name_fi, name_sv, name_en)
store_categories (store_id FK, category_slug FK, offer_count, synced_at)
products        (ean PK, base_ean, name_fi, name_sv, name_en, brand_name, section,
                 content_size, content_unit, net_weight, country_of_origin, is_domestic,
                 is_alcohol, is_internal_code, url_slug, image_url)
offers          (id PK, campaign_id, offer_name_id, offer_type, title_fi, title_sv, title_en,
                 image_url, normal_price, normal_unit)
offer_products  (offer_id FK, product_ean FK, position)   -- for multi-product offers
store_offers    (store_id FK, offer_id FK, synced_at, expires_at)
prices          (product_ean FK, store_id FK, kind [normal|discount|batch], price, unit,
                 discount_pct, discount_type, batch_amount, start_date, end_date,
                 campaign_id, discount_source, synced_at)
```

---

## Running & Testing

```bash
# Start server
flask --app scraper.py run --debug

# Run all tests (hits live K-Ruoka API, ~3–4 min)
python -m pytest tests/ -v

# Scripts (server must be running)
python scripts/health_check.py
python scripts/bulk_stores.py
python scripts/bulk_store_offers.py N110
python scripts/discover_all.py 3   # benchmark 3 stores
```
