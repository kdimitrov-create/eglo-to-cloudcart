# EGLO -> CloudCart sync

Scrapes [eglo.com/bg](https://www.eglo.com/bg/) and pushes products into a
CloudCart store via the GraphQL admin API. Designed to run on a schedule (every
8h via GitHub Actions) — only new products are created and only changed
products (by sitemap `<lastmod>`) are re-fetched.

Currently targets `bronson.cloudcart.net`.

## Architecture

```
sitemap_bg.xml ──► sitemap_parser ──► {product_code: lastmod}
                                            │
category pages ──► category_mapper ──► category_map.json
                                            │
                   mirror_categories ──► CloudCart category tree
                                            │
                                            ▼
state/products.json  ──►  sync.py  ──►  GraphQL mutations
        ▲                                   │
        └───────────────────────────────────┘
                  (state updated after each batch)
```

## Modules (`src/`)

| File | Purpose |
|---|---|
| `cloudcart_client.py` | GraphQL client (find/create/update product, category, image upload) |
| `sitemap_parser.py` | Parse `sitemap_bg.xml` -> products + categories with `lastmod` |
| `category_mapper.py` | Crawl all EGLO category pages, build product -> categories map (with full pagination) |
| `mirror_categories.py` | Create matching CloudCart categories with proper parent/child links |
| `eglo_to_cloudcart.py` | HTML parser — extracts title, price, description, tabs, images, EAN |
| `sync.py` | Main pipeline — diff sitemap vs `state/products.json`, create/update/deactivate |
| `fetch_products.py` | Manual helper — populate `cache/` for a list of SKUs |
| `test_import.py` | One-shot MVP test — imports 2 hardcoded SKUs |

## Local usage

```bash
pip install -r requirements.txt

export CLOUDCART_PAT=cc_pat_XXXXXXXXXXXX

# 1. Build / refresh category map (slow ~2 min, ~165 HTTP requests)
python src/category_mapper.py

# 2. Mirror categories into CloudCart (only creates missing ones)
python src/mirror_categories.py

# 3. Sync products (defaults to "all changed since last run")
python src/sync.py

# Useful flags
python src/sync.py --limit 10            # process only 10 changed products
python src/sync.py --no-images           # skip image uploads (faster iteration)
python src/sync.py --only 901565 110181  # restrict to specific SKUs
```

## Scheduled run (GitHub Actions)

`.github/workflows/sync.yml` runs every 8 hours. Required setup:

1. Repo Settings -> Secrets -> Actions -> add `CLOUDCART_PAT`
2. Enable Actions workflow permissions (Settings -> Actions -> Workflow permissions -> Read and write)

`state/products.json` is committed back after each run as the audit trail of
what was synced. New products on EGLO -> created. Removed products on EGLO ->
deactivated (`productsBulkSetActive: no`) but kept in CloudCart.

## State file format

```json
{
  "901565": {
    "cloudcart_id": "50621",
    "lastmod":     "2026-05-27T02:37:11+00:00",
    "price":       "101.24",
    "title":       "AMMONIAK - 901565"
  }
}
```

## Notes & limitations

- Prices are taken as-is from EGLO (EUR, VAT included).
- Stock/quantity is intentionally not tracked — products are imported without
  inventory control.
- Categories are detected by crawling category pages, not from the product HTML
  (Magento renders breadcrumbs via JS). Pagination uses the toolbar `total
  count` since the visible pagination only shows the first 5 page links.
- `assignPropertyToProduct` is destructive (replace-all) and is currently not
  used — characteristics are visible as tab content but not as filterable
  properties yet.
